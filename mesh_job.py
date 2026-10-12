#!/usr/bin/env python3
"""
Download NOAA's latest MRMS MESH 24-hour maximum hail grid and turn it into a
map overlay the hail map page can show.

MESH (Maximum Estimated Size of Hail) is a radar-derived estimate on a ~1 km grid
covering the contiguous U.S. This job keeps the largest value seen at each point
over the past 24 hours, which is what a "swath" map shows.

Outputs (in --out):
  mesh_24h.png    color overlay, transparent below 0.75 inch, in Web Mercator rows
                  so Leaflet's imageOverlay lines up with the basemap
  mesh_meta.json  valid time, bounds, largest value, source file

Usage:
  python mesh_job.py --out site
  python mesh_job.py --out site --local MRMS_MESH_Max_1440min_00.50_20261012-203000.grib2.gz

Data: NOAA Open Data Dissemination, S3 bucket noaa-mrms-pds (public, no key needed).
"""
import argparse
import datetime as dt
import gzip
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT_PREFIX = "CONUS/MESH_Max_1440min_00.50"

# Same colors and breakpoints as the page legend (inches).
TIERS = [
    (0.75, (0xF5, 0xC5, 0x42)),
    (1.00, (0xF0, 0x8A, 0x24)),
    (1.50, (0xE0, 0x4A, 0x2B)),
    (2.00, (0xC2, 0x18, 0x5B)),
    (2.75, (0x8E, 0x24, 0xAA)),
    (4.00, (0x45, 0x27, 0xA0)),
]
ALPHA = 205
MM_PER_INCH = 25.4
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


# ---------------------------------------------------------------- S3 helpers
def _get(url, **kw):
    import requests
    r = requests.get(url, timeout=60, headers={"User-Agent": "hail-mesh-job/1.0"}, **kw)
    r.raise_for_status()
    return r


def list_keys(prefix):
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(_get(BUCKET + "/", params=params).content)
        keys += [e.text for e in root.findall(".//s:Contents/s:Key", S3_NS)]
        if root.findtext("s:IsTruncated", namespaces=S3_NS) != "true":
            return keys
        token = root.findtext("s:NextContinuationToken", namespaces=S3_NS)


def list_dirs(prefix):
    params = {"list-type": "2", "prefix": prefix, "delimiter": "/"}
    root = ET.fromstring(_get(BUCKET + "/", params=params).content)
    return [e.text for e in root.findall(".//s:CommonPrefixes/s:Prefix", S3_NS)]


def find_latest_key(now):
    """Newest file for today (UTC), falling back to yesterday around midnight."""
    for back in (0, 1):
        day = now - dt.timedelta(days=back)
        keys = [k for k in list_keys(f"{PRODUCT_PREFIX}/{day:%Y%m%d}/") if k.endswith(".grib2.gz")]
        if keys:
            return sorted(keys)[-1]
    dirs = [d for d in list_dirs("CONUS/") if "MESH" in d]
    raise SystemExit(
        f"No files found under {PRODUCT_PREFIX}/. MESH folders that do exist: {dirs or 'none listed'}. "
        "Adjust PRODUCT_PREFIX at the top of this file."
    )


def valid_time_from_key(key):
    m = re.search(r"(\d{8})-(\d{6})", key)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)


# --------------------------------------------------------------- GRIB2 decode
def read_grid(grib_bytes):
    """Decode one regular lat/lon GRIB2 message.

    Returns (inches, north, south, west, east): inches is a 2D float array with row 0
    at the north edge and column 0 at the west edge; bounds are cell edges in degrees
    (longitude -180..180). Missing or no-coverage cells come back as 0.
    """
    import eccodes

    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(grib_bytes)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                raise ValueError("No GRIB message found in file")
            try:
                ni = eccodes.codes_get_long(gid, "Ni")
                nj = eccodes.codes_get_long(gid, "Nj")
                lat1 = eccodes.codes_get_double(gid, "latitudeOfFirstGridPointInDegrees")
                lat2 = eccodes.codes_get_double(gid, "latitudeOfLastGridPointInDegrees")
                lon1 = eccodes.codes_get_double(gid, "longitudeOfFirstGridPointInDegrees")
                lon2 = eccodes.codes_get_double(gid, "longitudeOfLastGridPointInDegrees")
                di = eccodes.codes_get_double(gid, "iDirectionIncrementInDegrees")
                dj = eccodes.codes_get_double(gid, "jDirectionIncrementInDegrees")
                j_pos = eccodes.codes_get_long(gid, "jScansPositively")
                i_neg = eccodes.codes_get_long(gid, "iScansNegatively")
                vals = eccodes.codes_get_values(gid)
            finally:
                eccodes.codes_release(gid)

    if vals.size != ni * nj:
        raise ValueError(f"Expected {ni}x{nj} values, got {vals.size}")
    grid = vals.reshape(nj, ni).astype(np.float32)
    if j_pos:           # first row is the southern edge: flip so row 0 is north
        grid = grid[::-1]
    if i_neg:           # first column is the eastern edge: flip so column 0 is west
        grid = grid[:, ::-1]
        lon1, lon2 = lon2, lon1

    north = max(lat1, lat2) + dj / 2
    south = min(lat1, lat2) - dj / 2
    west = lon1 - di / 2
    east = lon2 + di / 2
    if west > 180:
        west -= 360
    if east > 180:
        east -= 360

    # MRMS marks "no data" / "no coverage" with negative numbers; mm -> inches.
    bad = ~np.isfinite(grid) | (grid < 0) | (grid > 9000)
    inches = np.where(bad, 0.0, grid / MM_PER_INCH).astype(np.float32)
    return inches, round(north, 4), round(south, 4), round(west, 4), round(east, 4)


# ------------------------------------------------------------------ rendering
def max_pool(a, f):
    """Shrink by an integer factor keeping the largest value, so small hail cores survive."""
    nj, ni = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    a = a[:nj, :ni]
    return a.reshape(nj // f, f, ni // f, f).max(axis=(1, 3))


def merc_y(lat_deg):
    return math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def to_mercator_rows(a, north, south, west, east):
    """Resample rows so image height is linear in Web Mercator, as imageOverlay assumes.

    Columns are already linear in longitude. Returns the resampled array.
    """
    h, w = a.shape
    lat_step = (north - south) / h
    span = math.radians(east - west)
    yn, ys = merc_y(north), merc_y(south)
    out_h = max(1, int(round(w * (yn - ys) / span)))
    ys_rows = yn - (np.arange(out_h) + 0.5) * (yn - ys) / out_h
    lats = np.degrees(2 * np.arctan(np.exp(ys_rows)) - math.pi / 2)
    src = np.clip(((north - lats) / lat_step).astype(int), 0, h - 1)
    return a[src, :]


def render_png(inches, path):
    h, w = inches.shape
    idx = np.zeros((h, w), dtype=np.uint8)
    for i, (floor, _) in enumerate(TIERS, start=1):
        idx[inches >= floor - 1e-9] = i
    img = Image.frombytes("P", (w, h), idx.tobytes())
    palette = [0, 0, 0]
    for _, rgb in TIERS:
        palette += list(rgb)
    palette += [0] * (768 - len(palette))
    img.putpalette(palette)
    img.save(path, optimize=True, transparency=bytes([0] + [ALPHA] * len(TIERS)))


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output folder (default: site)")
    ap.add_argument("--local", help="use this .grib2 or .grib2.gz file instead of downloading")
    ap.add_argument("--downsample", type=int, default=2, help="shrink factor, 2 = about 2 km pixels")
    ap.add_argument("--max-age-hours", type=float, default=6.0, help="fail if the newest file is older than this")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if args.local:
        key = os.path.basename(args.local)
        raw = open(args.local, "rb").read()
    else:
        key = find_latest_key(now)
        raw = _get(f"{BUCKET}/{key}").content
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    valid = valid_time_from_key(key)
    if valid and not args.local:
        age_h = (now - valid).total_seconds() / 3600
        if age_h > args.max_age_hours:
            raise SystemExit(f"Newest MESH file is {age_h:.1f} hours old ({key}); not publishing stale data.")

    inches, north, south, west, east = read_grid(data)
    small = max_pool(inches, max(1, args.downsample))
    merc = to_mercator_rows(small, north, south, west, east)

    os.makedirs(args.out, exist_ok=True)
    png_path = os.path.join(args.out, "mesh_24h.png")
    render_png(merc, png_path)

    meta = {
        "product": "NOAA MRMS MESH, 24-hour maximum",
        "source_key": key,
        "valid_utc": valid.isoformat() if valid else None,
        "generated_utc": now.isoformat(),
        "bounds": [[south, west], [north, east]],
        "image": "mesh_24h.png",
        "pixels": [int(merc.shape[1]), int(merc.shape[0])],
        "max_inches": round(float(inches.max()), 2),
    }
    with open(os.path.join(args.out, "mesh_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {png_path} ({os.path.getsize(png_path) / 1024:.0f} KB), max {meta['max_inches']} in, valid {meta['valid_utc']}")


if __name__ == "__main__":
    sys.exit(main())
