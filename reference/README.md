# Reference material

Waveshare's own files for the JetBot AI Kit, from
https://www.waveshare.com/wiki/JetBot_AI_Kit

| File | What | In git |
|---|---|---|
| `JetBot_schematic.pdf` | the expansion board schematic, 2020-02-28 | yes |
| `step_tree.json` | every solid in the CAD with its size and position, written by `tools/inspect_step.py` | yes |
| `jetbot.STEP` | the full CAD assembly, 90 MB | no: download it |

To get the STEP file back after cloning:

```bash
cd reference
curl -LO https://files.waveshare.com/upload/4/49/Jetbot_3D_Drawing.zip
unzip Jetbot_3D_Drawing.zip
```

It is only needed to rebuild `web/jetbot.glb` and `export/meshes/`, which are
in git.
