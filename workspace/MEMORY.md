# MEMORY.md

## OpenClaw EC2 baseline

- Host: Ubuntu 22.04 on EC2 `t2.micro`
- OpenClaw installed globally under `~/.npm-global`
- Gateway is configured in local mode with token auth and loopback bind
- systemd user service is installed and linger is enabled for `ubuntu`

## Recovery lesson

- This host is small enough that large npm installs can stall SSH.
- A persistent `2G` swapfile was added to reduce recurrence.
- If SSH accepts TCP but hangs before banner, check host saturation before blaming security groups.
