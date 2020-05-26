FROM python:3.6-alpine
RUN apk add --no-cache python3-dev openssl-dev libffi-dev musl-dev make gcc g++ zeromq curl libtool autoconf automake
RUN pip install --no-cache-dir anthill-game-controller
COPY brainout/anthill.pub ./
ENTRYPOINT [ "python", "-m", "anthill.game.controller.server"]
