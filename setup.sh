#!/bin/bash
#
# Optional setup: installs OpenJDK 21 for the map app's route planning.
#
# This used to also download the online shop's product dataset from Google
# Drive and a spaCy model for its reward function. The shop now seeds its
# catalog from `config/apps/onlineshop/content/` and searches with SQLite
# FTS5, so it needs no setup at all -- it works straight after `uv sync`.
#
# The map app still shells out to OpenTripPlanner (`otp-2.6.0-shaded.jar`),
# which is Java, so the JDK install moved here from the shop's directory.
# Skip this script entirely if you do not need `apps.maps.allow_planning`.

set -euo pipefail

JAVA_DIR="src/open_apps/apps/map_app/java"
mkdir -p "$JAVA_DIR"
cd "$JAVA_DIR"

# Download and install Java 21 from https://jdk.java.net/archive/
ARCH="$(uname -m)"
if [ "$ARCH" = "x86_64" ]; then
  echo "Intel/AMD machine"
  wget https://download.java.net/java/GA/jdk21.0.1/415e3f918a1f4062a0074a2794853d0d/12/GPL/openjdk-21.0.1_linux-x64_bin.tar.gz
  tar -xzf openjdk-21.0.1_linux-x64_bin.tar.gz
elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
  echo "ARM64 machine"
  curl -O https://download.java.net/java/GA/jdk21.0.1/415e3f918a1f4062a0074a2794853d0d/12/GPL/openjdk-21.0.1_macos-aarch64_bin.tar.gz
  tar -xzf openjdk-21.0.1_macos-aarch64_bin.tar.gz
else
  echo "[System Not Supported]: Currently only x64 and Mac ARM64 are supported."
  exit 1
fi

echo "Java 21 installed. Run 'source setup_javapath.sh' to put it on PATH."
