# macOS FFmpeg bundles

Runtime selection uses the host operating system and CPU architecture:

- `arm64/ffmpeg`, `arm64/ffprobe`: standalone Apple Silicon builds, version 9.0.
- `x86_64/ffmpeg`, `x86_64/ffprobe`: standalone Intel builds, version 8.0.

The executables depend on macOS system libraries, not on the development Conda
installation. The original Windows bundle remains under `../windows/`.

Each architecture has a `manifest.json` recording its upstream URLs and SHA256
checksums. Downloads are from [OSXExperts](https://www.osxexperts.net/), which also
provides the source/build references and applicable upstream information. The
checksums are verified against the extracted executable before installation.

To restore the host's bundle, from the repository root:

```bash
python script/setup_ffmpeg.py
```

To prepare both architectures:

```bash
python script/setup_ffmpeg.py --arch all
```

The installer refuses to overwrite files whose contents differ from the pinned
checksum. An upstream build update requires reviewing its source and updating the
pinned URL/checksum in `script/setup_ffmpeg.py`.

`app.runtime_paths.configure_runtime_environment()` puts only matching platform
and architecture directories on PATH and sets `FFMPEG_BIN` and `FFPROBE_BIN`.
Explicit existing environment overrides are retained. When no matching bundle
is installed, normal PATH discovery is used.

Apple Silicon execution and media operations were verified on the development
Mac. Intel selection is covered by unit tests; the Intel binaries were checked
for architecture and checksum, not executed on an Intel Mac in this session.
