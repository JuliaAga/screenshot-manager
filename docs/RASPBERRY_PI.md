# Raspberry Pi installation

Use a supported Raspberry Pi OS/Debian installation, Git, and Docker with Compose. ARMv7 was tested on a Raspberry Pi 2 with Bookworm; ARM64/AMD64 container builds remain unverified. Model inference runs on a separate computer or a configured cloud provider.

## Install

```sh
git clone https://github.com/JuliaAga/screenshot-manager.git
cd screenshot-manager
cp .env.example .env
docker compose up -d --build
```

For standalone Compose use `sudo docker-compose -f compose.yaml` instead of `docker compose`. Use that same command consistently so it selects the same project and data volume.

The app binds to loopback by default. From your computer, run:

```sh
ssh -L 8787:127.0.0.1:8787 YOUR_USER@YOUR_PI
```

Open http://127.0.0.1:8787 and create a password. Configure your AI provider in Settings & backup. For access from other devices, follow the private HTTPS/Tailscale instructions in the [README](../README.md).

## Maintenance

Run from the cloned repository:

```sh
docker compose ps
docker compose logs --tail=50 later
docker compose restart later
```

To update:

```sh
git pull --ff-only
docker compose up -d --build
```

Normal rebuilds preserve the named data volume. `docker compose down -v` deletes it. The restart policy is `unless-stopped`; enable the Docker daemon at boot using your OS service manager.

Some Pi kernels do not support memory cgroups, so the requested 384 MB limit may not be enforced. Check Docker warnings, available memory, and disk usage. Large images and OCR can exhaust a Pi 2’s resources.

## Full backup

This archive contains credentials, sessions, and personal screenshots. Keep it private and outside the source repository.

```sh
docker compose stop later
docker compose run --rm --no-deps -T later tar -czf - -C /data . > later-server-backup.tar.gz
docker compose start later
```

See the [README backup instructions](../README.md#backup-and-restore) for restore requirements and portable collection exports.
