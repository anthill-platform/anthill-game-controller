# Game Controller
Game Controller is a second part of Game Service. 

This project is docker-ready. To install it on any host, install docker first, then create this Dockerfile:
```dockerfile
FROM python:3.6-alpine
RUN apk add --no-cache python3-dev openssl-dev libffi-dev musl-dev make gcc g++ zeromq zeromq-dev curl libtool autoconf automake
RUN pip install --no-cache-dir anthill-game-controller
COPY anthill.pub ./
ENV connection_password=<password>
CMD [ "python", \
    "-m", "anthill.game.controller.server", \
    "--auth-key-public=anthill.pub", \
    "--discovery-service=https://<your public discovery service>", \
    "--connection-username=<username>", \
    "--connection-gamespace=<gamespace-alias>", \
    "--gs-host=<host ip>"]
```

Make sure to pup `anthill.pub` next to it. Then run:
```bash
docker build -t anthill-game-controller .
```

Once the image is built, run it:
```bash
docker run -d --name anthill-game-controller --network host --restart=always -i \
    -v /var/log/gameserver:/var/log/gameserver \
    -v /usr/local/anthill:/usr/local/anthill \
    anthill-game-controller
```

See <a href="https://github.com/anthill-platform/anthill-game-master#game-service">Game Service</a> for more information.

## API

Please refer to the <a href="doc/API.md">API Documentation</a> for more information.

## Overall Architecture

<center>
<img src="https://cloud.githubusercontent.com/assets/1666014/26266946/613bc5a0-3cf0-11e7-9c1e-59e403ea5bdd.png" width="954">
</center>