#!/bin/sh
set -eu

docker compose down --remove-orphans --volumes
container_ids="$(docker ps -aq)"
if [ -n "$container_ids" ]; then
  docker rm -f $container_ids
fi
sudo docker system prune --all --volumes --force
rm -rf "${MOUNT_DOCKER_DATA_ROOT:-./mount_docker_data}" docker_data
