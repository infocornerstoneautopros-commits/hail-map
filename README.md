# Hail map with NOAA radar hail swaths

- `hail-swath-map.html`: the map page (storm reports, warnings, radar, city list)
- `mesh_job.py`: downloads NOAA's latest MRMS MESH 24-hour hail grid and writes `mesh_24h.png` and `mesh_meta.json`
- `.github/workflows/mesh.yml`: runs the job every 10 minutes and publishes everything to GitHub Pages
- `hail-proxy-worker.js`: optional CORS proxy for the SPC report files (see the top of the file)

## Option A: GitHub Pages (hosts the page and the overlay)

1. Create a **public** GitHub repo and upload everything in this folder, keeping `.github/workflows/mesh.yml` in place. (Public repos get unlimited Actions minutes; a private repo would use roughly 4,000+ minutes a month at this schedule.)
2. Repo Settings > Pages > Source: **GitHub Actions**.
3. Actions tab > "Update hail map" > Run workflow. When it finishes, your page is at the Pages URL.
4. Optional: embed it on your own site with an iframe, or copy the page there and set `CONFIG.mesh.baseUrl` to your Pages URL.

## Option B: your own server

Run `pip install -r requirements.txt`, then schedule `python mesh_job.py --out /path/to/web/folder` every 10 minutes (cron). Put `hail-swath-map.html` in the same folder and set `CONFIG.mesh.baseUrl` to `"./"`.

## If the job can't find a file

NOAA's folder names are set at the top of `mesh_job.py` (`PRODUCT_PREFIX`). If NOAA renames them, the job prints the MESH folders that do exist so you can update that one line.

## Notes

- The overlay is the largest MESH value at each point over the past 24 hours, so it only appears in the page's Today view.
- Data is public NOAA data (bucket `noaa-mrms-pds`), no key required.
