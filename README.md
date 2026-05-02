# Attendance Counter 🌴

Real-time attendance counter using YOLO + line-crossing tracking. Runs entirely on your Mac — no cloud, no images leave your device.

## Setup

```bash
./scripts/setup.sh   # one-time install + build
./scripts/run.sh     # start the service
open http://localhost:8765
```

For frontend hot-reload during development:

```bash
./scripts/run.sh --dev
# Dashboard: http://localhost:5173
# API:       http://localhost:8765
```

## Requirements

- macOS 13+ (Apple Silicon recommended; Intel and CUDA also work)
- Python 3.11 or 3.12
- Node.js 18+ and npm
- A camera (built-in, USB, or RTSP/HTTP)

## Configuration

Copy `counter/.env.example` to `counter/.env` and tweak the camera source, model, or bind address. All settings can also be passed as environment variables.

## Credits

Frontend foundation by [@berrysauce](https://github.com/berrysauce). Thanks!

## License

MIT — see [LICENSE](LICENSE).
