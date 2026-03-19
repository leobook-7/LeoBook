#!/usr/bin/env bash
set -euo pipefail

echo "=== LeoBook Codespace Auto-Setup (API 36) ==="

# ---- 1. Python Dependencies ----
echo "[1/7] Installing Python dependencies..."
pip install --upgrade pip -q
[ -f requirements.txt ] && pip install -r requirements.txt -q
[ -f requirements-rl.txt ] && pip install -r requirements-rl.txt -q

# ---- 2. Playwright ----
echo "[2/7] Installing Playwright browsers..."
python -m playwright install-deps 2>/dev/null || true
python -m playwright install chromium

# ---- 3. Create Data Directories ----
echo "[3/7] Creating data directories..."
mkdir -p Data/Store/{models,Assets}
mkdir -p Data/Store/crests/{teams,leagues,flags}
mkdir -p Modules/Assets/{logos,crests}

# ---- 4. Flutter SDK ----
echo "[4/7] Installing Flutter SDK..."
if [ ! -d "$HOME/flutter" ]; then
    git clone https://github.com -b stable "$HOME/flutter" --depth 1
fi
export PATH="$PATH:$HOME/flutter/bin"
grep -q 'flutter/bin' ~/.bashrc || echo 'export PATH="$PATH:$HOME/flutter/bin"' >> ~/.bashrc
$HOME/flutter/bin/flutter precache --android

# ---- 5. Android SDK (Targeting API 36) ----
echo "[5/7] Configuring Android SDK 36..."
# Use the ANDROID_HOME provided by the devcontainer feature
export ANDROID_HOME="/home/vscode/android-sdk"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"

# Accept all licenses
echo "  Accepting Android licenses..."
yes | sdkmanager --licenses > /dev/null 2>&1 || true

# Install specific API 36 components
echo "  Downloading Platform 36 and Build-Tools..."
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0" > /dev/null 2>&1

# Link Flutter to Android SDK
$HOME/flutter/bin/flutter config --android-sdk "$ANDROID_HOME"

# ---- 6. Flutter App Dependencies & Gradle Update ----
echo "[6/7] Updating Flutter project to SDK 36..."
if [ -d "leobookapp" ]; then
    cd leobookapp
    # Auto-update build.gradle to target 36
    find . -name "build.gradle" -exec sed -i 's/compileSdk .*/compileSdk 36/' {} + 2>/dev/null || true
    find . -name "build.gradle" -exec sed -i 's/targetSdk .*/targetSdk 36/' {} + 2>/dev/null || true
    
    $HOME/flutter/bin/flutter pub get
    cd ..
fi

# ---- 7. VS Code Settings ----
mkdir -p .vscode
[ ! -f .vscode/settings.json ] && echo '{"python.terminal.useEnvFile": true}' > .vscode/settings.json

echo "============================================"
echo "  LeoBook Setup Complete! (Ready for API 36)"
echo "============================================"
