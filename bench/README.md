# Benchmarks

Tools used to find and verify the map's performance bottlenecks. They drive a headless Chrome on the real GPU
through the DevTools protocol.

```bash
chrome --headless=new --remote-debugging-port=9222 --user-data-dir=/tmp/mp-chrome --window-size=1600,1000 \
       --use-angle=d3d11 --enable-gpu --ignore-gpu-blocklist about:blank
# add --disable-gpu-vsync --disable-frame-rate-limit to see true frame cost (but expect queueing artefacts)
# add --force-device-scale-factor=1.5 to emulate a scaled laptop display

id=$(curl -s -X PUT "127.0.0.1:9222/json/new?http://127.0.0.1:8080/?debug=1" | jq -r .id)
node bench/panbench.mjs  $id 3          # camera pans through MapLibre, frame-time stats
node bench/dragbench.mjs $id 3          # real mouse drag (press / move / release)
node bench/cpuprof.mjs   $id 3000 [fn]  # main-thread profile; optional call chains into a function
node bench/eval.mjs      $id "$(cat bench/glbench.js)"   # raw WebGL2 throughput ceiling for this GPU
node bench/console.mjs   $id            # console errors after a reload
```

Page switches (with `?debug=1`): `nobasemap=1`, `nopeople=1`, `nolod=1`, `fullstyle=1`, `scale=N`.
