FROM python:3.10.10-alpine3.17
# FROM python:3.9.16-alpine3.17
# FROM python:3.7.3-alpine OLD

RUN apk add --update build-base git libxslt-dev linux-headers make nodejs-current npm zeromq zeromq-dev


ENV USER tranql
ENV HOME /home/$USER

RUN addgroup -S $USER && adduser -S $USER -G $USER -s /bin/bash -h $HOME

USER $USER
WORKDIR $HOME

ENV PATH=$HOME/.local/bin:$PATH
ENV PUBLIC_URL={{web_prefix}}
ENV BACKPLANE=http://tranql-backplane.renci.org

COPY --chown=$USER . tranql/

# WORKDIR $HOME/tranql/src/tranql/web
# RUN npm install
# RUN GENERATE_SOURCEMAP=false npm run build

WORKDIR $HOME/tranql
RUN pip install --user --upgrade pip
RUN pip install --user -r requirements.txt
ENV PYTHONPATH=$HOME/tranql/src/
