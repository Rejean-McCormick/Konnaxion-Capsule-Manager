# Netcup progress v5

Fixes an intermittent Windows/OpenSSH stall observed during **Deploy Droplet** at the remote runtime preparation step.

- SSH connection establishment now uses `ConnectTimeout=10` and `ConnectionAttempts=1` in the GUI execution client. Long Agent operations keep their existing operation-level timeouts.
- The idempotent `mkdir -p` remote runtime preparation is retried up to 3 times, with progress messages between retries.
- Each runtime-preparation attempt is capped at 30 seconds instead of leaving the progress page apparently stuck for 120 seconds.
- Non-timeout SSH failures (authentication, permissions, host key, command failure) are not retried and are surfaced immediately.
