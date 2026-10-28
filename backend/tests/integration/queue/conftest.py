"""Un `redis-server` real, efímero y con contraseña, por sesión de tests. Se salta si el
binario no está instalado; nunca toca un Redis ya existente."""

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis

PASSWORD = "it-redis-pw"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def redis_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server is not installed")
    port = _free_port()
    workdir: Path = tmp_path_factory.mktemp("redis")
    process = subprocess.Popen(  # noqa: S603 - binario y argumentos fijos del propio test
        [
            binary,
            "--port",
            str(port),
            "--bind",
            "127.0.0.1",
            "--requirepass",
            PASSWORD,
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(workdir),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"redis://default:{PASSWORD}@127.0.0.1:{port}/0"
    try:
        client = redis.Redis.from_url(url)
        for _ in range(100):
            try:
                client.ping()
                break
            except redis.exceptions.RedisError:
                time.sleep(0.05)
        else:
            pytest.fail("redis-server did not start")
        client.close()
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def clean_redis(redis_url: str) -> Iterator[str]:
    client = redis.Redis.from_url(redis_url)
    client.flushall()
    yield redis_url
    client.flushall()
    client.close()
