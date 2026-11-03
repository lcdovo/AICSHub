"""只新建本次专属隔离验收资源，不清理/重用业务容器或卷。"""

from datetime import datetime
import json, secrets, socket, subprocess, time
from pathlib import Path


def call(args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("isolated_docker_setup_failed:" + args[0])
    return result.stdout.strip()


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "reports/m3-closeout/runtime"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "state.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        for cid in state["containers"].values():
            assert (
                call(
                    [
                        "inspect",
                        "--format",
                        '{{index .Config.Labels "aicshub.test.owner"}}',
                        cid,
                    ]
                )
                == "m3-closeout"
            )
        print("Owned M3 resources already recorded")
        return
    prefix = "aicshub-m3-acceptance-" + datetime.now().strftime("%Y%m%d%H%M%S")
    secret = secrets.token_hex(20)
    ports = {}
    for kind in ("mysql", "redis", "milvus"):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            ports[kind] = str(sock.getsockname()[1])
    state = {"prefix": prefix, "secret": secret, "ports": ports, "containers": {}}
    call(["network", "create", "--label", "aicshub.test.owner=m3-closeout", prefix])
    env = out / "mysql.env"
    env.write_text(
        "MYSQL_ROOT_PASSWORD=" + secret + "\nMYSQL_DATABASE=aicshub_m3_acceptance\n",
        encoding="utf-8",
    )

    def start(kind, args):
        cid = call(
            [
                "run",
                "-d",
                "--name",
                prefix + "-" + kind,
                "--label",
                "aicshub.test.owner=m3-closeout",
                "--network",
                prefix,
                "--network-alias",
                kind,
                *args,
            ]
        )
        state["containers"][kind] = cid
        path.write_text(json.dumps(state), encoding="utf-8")

    start(
        "mysql",
        [
            "--tmpfs",
            "/var/lib/mysql:rw,size=512m",
            "-p",
            "127.0.0.1:" + ports["mysql"] + ":3306",
            "--env-file",
            str(env),
            "mysql:8.0",
        ],
    )
    start(
        "redis",
        [
            "-p",
            "127.0.0.1:" + ports["redis"] + ":6379",
            "redis:7-alpine",
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
        ],
    )
    start(
        "etcd",
        [
            "--tmpfs",
            "/etcd:rw,size=128m",
            "quay.io/coreos/etcd:v3.5.18",
            "etcd",
            "--advertise-client-urls=http://etcd:2379",
            "--listen-client-urls=http://0.0.0.0:2379",
            "--data-dir=/etcd",
        ],
    )
    start(
        "minio",
        [
            "--tmpfs",
            "/data:rw,size=512m",
            "-e",
            "MINIO_ROOT_USER=syntheticm3",
            "-e",
            "MINIO_ROOT_PASSWORD=" + secret,
            "minio/minio:RELEASE.2023-03-20T20-16-18Z",
            "server",
            "/data",
        ],
    )
    start(
        "milvus",
        [
            "--security-opt",
            "seccomp:unconfined",
            "--tmpfs",
            "/var/lib/milvus:rw,size=768m",
            "-p",
            "127.0.0.1:" + ports["milvus"] + ":19530",
            "-e",
            "ETCD_ENDPOINTS=etcd:2379",
            "-e",
            "MINIO_ADDRESS=minio:9000",
            "-e",
            "MINIO_ACCESS_KEY_ID=syntheticm3",
            "-e",
            "MINIO_SECRET_ACCESS_KEY=" + secret,
            "milvusdb/milvus:v2.5.10",
            "milvus",
            "run",
            "standalone",
        ],
    )
    print(
        json.dumps(
            {
                "prefix": prefix,
                "ports": ports,
                "containers_created": len(state["containers"]),
            }
        )
    )


if __name__ == "__main__":
    main()
