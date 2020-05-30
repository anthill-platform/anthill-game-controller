FROM python:3.6-alpine
RUN apk add --no-cache python3-dev openssl-dev libffi-dev musl-dev make gcc g++ zeromq zeromq-dev curl libtool autoconf automake
WORKDIR /tmp
COPY anthill /tmp/anthill
COPY setup.py /tmp
RUN python setup.py install
RUN rm -rf /tmp
ENTRYPOINT [ "python", "-m", "anthill.game.controller.server"]
