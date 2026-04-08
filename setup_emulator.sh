#!/bin/bash
# setup_emulator.sh — One-time Android emulator setup
# Run this after installing Java (brew install --cask temurin@17 requires sudo/password)
# 
# Usage: bash setup_emulator.sh

set -e

echo "=== MMT-OS Android Emulator Setup ==="

# Check Java
if ! java -version 2>/dev/null; then
  echo ""
  echo "ERROR: Java not found."
  echo "Install it by running in your terminal:"
  echo "  brew install --cask temurin@17"
  echo "  (Requires your Mac password)"
  exit 1
fi

# Accept SDK licenses
echo "Accepting SDK licenses..."
yes | sdkmanager --sdk_root="$HOME/Library/Android/sdk" --licenses > /dev/null 2>&1 || true

# Install required SDK components
echo "Installing emulator + system image (this may take a few minutes)..."
sdkmanager --sdk_root="$HOME/Library/Android/sdk" \
  "platform-tools" \
  "emulator" \
  "system-images;android-34;google_apis;arm64-v8a" \
  "platforms;android-34"

# Create AVD
echo "Creating AVD 'mmt_test'..."
echo "no" | avdmanager create avd \
  --name "mmt_test" \
  --package "system-images;android-34;google_apis;arm64-v8a" \
  --device "pixel_6" \
  --force

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "To start the emulator:"
echo "  ~/Library/Android/sdk/emulator/emulator -avd mmt_test -no-snapshot-load &"
echo ""
echo "Then wait ~30s for it to boot, and run:"
echo "  adb devices  (should show emulator-5554)"
