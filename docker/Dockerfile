FROM databricksruntime/python:15.4-LTS

RUN pip install pyspark==3.5.3 delta-spark==3.2.1 pytest build \
    && ln -sf "$(command -v python3.11)" /usr/local/bin/python

WORKDIR /workspace
