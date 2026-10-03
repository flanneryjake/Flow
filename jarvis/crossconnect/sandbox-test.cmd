@echo off
rem Docker sandbox run of the node agent tests on the rig (Jake's rule: medium-risk code is tested in Docker first).
rem Nothing on the rig is touched: the folder is mounted read-only and the container has no network.
cd /d "%~dp0"
docker run --rm --network none -v "%cd%":/w:ro -w /w -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim ^
  sh -c "cp -r /w /t && cd /t && python -m unittest test_node_agent -v"
