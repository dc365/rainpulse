# Pinned native radar detector source subset

This directory contains unmodified files needed to build the original `detect_emitters`
and `detect_emitters2` C detector cores from **BALTRAD bRopo**. It is NOT a full
RAVE/ODIM installation or the `_ropogenerator` Python module.

- bRopo revision: `c330b05ff55d4075a4d9e8fb404f17b3e459e049`
  https://github.com/baltrad/bropo/tree/c330b05ff55d4075a4d9e8fb404f17b3e459e049
- RAVE supporting source revision: `88458bc72bf6a3e2bdb0ae0856d4fba3a2550f0e`
  https://github.com/baltrad/rave/tree/88458bc72bf6a3e2bdb0ae0856d4fba3a2550f0e

Original copyright and licensing notices, `COPYING`, `COPYING.LESSER` and `LICENSE`
are retained in each upstream directory. Read the applicable notices before
redistributing binaries. `sources.lock.json` pins the included original files.
The original detector sources have not been edited. The process wrapper and build
recipe live separately in `tools/radar_native/` and are RainPulse integration code.

Build offline with GCC on Linux:

```bash
python tools/radar_native/build.py --output /absolute/new/path/emitter-core
```

The build verifies source hashes, rejects additional unlocked compilation inputs,
records compiler/source/wrapper/binary identity and does not download dependencies.
The executable is isolated by subprocess and never restores or interpolates DBZH.
The build is a source-level C reference, not evidence of radar skill, certification,
RAVE ABI compatibility or full application equivalence. Per-process native calls
are an offline reference implementation, not a proposed realtime worker topology.
