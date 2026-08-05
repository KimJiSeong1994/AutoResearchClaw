# MEMORY.md

## Hermes EC2 baseline

- Host: Ubuntu 22.04 on EC2 `t2.micro`
- Hermes is installed under `~/.hermes/hermes-agent`
- Gateway is configured with token auth and loopback bind on `127.0.0.1:28789`
- OpenClaw services and `~/.openclaw` are disabled rollback surfaces, not production paths
- systemd user service is installed and linger is enabled for `ubuntu`

## Recovery lesson

- This host is small enough that large npm installs can stall SSH.
- A persistent `2G` swapfile was added to reduce recurrence.
- If SSH accepts TCP but hangs before banner, check host saturation before blaming security groups.
