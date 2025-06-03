# mt5docker

Dockerized MetaTrader 5 terminal for Linux via Wine.

## How To Use
1. Clone the repo and make sure Docker is installed.
2. Copy `.env.example` to `.env` and fill in your broker credentials.
3. Run: `docker compose up -d`
4. Access MT5 at http://localhost:6081/vnc.html
5. Complete the interactive MT5 installation.
