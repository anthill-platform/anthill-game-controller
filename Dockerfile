FROM anthillplatform/anthill-common:latest
WORKDIR /tmp
COPY anthill /tmp/anthill
COPY setup.py /tmp
RUN python setup.py install
RUN rm -rf /tmp
ENTRYPOINT [ "python", "-m", "anthill.game.controller.server"]
