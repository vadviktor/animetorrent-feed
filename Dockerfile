FROM python:3.8

# Avoid warnings by switching to noninteractive
ENV DEBIAN_FRONTEND=noninteractive

COPY requirements*.txt /opt/

RUN apt-get update \
  && apt-get -y install --no-install-recommends apt-utils dialog 2>&1 \
  && apt-get -y install git iproute2 procps lsb-release \
  build-essential nano byobu psmisc less htop \
  # python
  && python -m pip install -U pip \
  && python -m pip install -r /opt/requirements.txt \
  && python -m pip install -r /opt/requirements.local.txt \
  # Clean up
  && apt-get autoremove -y \
  && apt-get clean -y \
  && rm -rf /var/lib/apt/lists/*

COPY .devcontainer/bashrc /root/.bashrc
COPY .devcontainer/.pythonrc.py /root/

# Switch back to dialog for any ad-hoc use of apt-get
ENV DEBIAN_FRONTEND=dialog
