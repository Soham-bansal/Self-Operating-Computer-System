@echo off
REM Step 4 - download OmniParser model weights (about 1 GB). Resumable; skips existing files.
cd /d "%~dp0.."
set "STATE=%cd%\.setup_state"
if not exist "%STATE%" mkdir "%STATE%"

echo === Step 4/4 : model weights, about 1 GB ===
if not exist env_omniparser\Scripts\python.exe (
    echo [ERROR] OmniParser environment missing. Run setup.bat - it does step 3 first.
    pause
    exit /b 1
)
call env_omniparser\Scripts\activate.bat
cd OmniParser
for %%F in ("icon_detect/train_args.yaml" "icon_detect/model.pt" "icon_detect/model.yaml") do (
    huggingface-cli download microsoft/OmniParser-v2.0 %%F --local-dir weights
)
for %%F in ("icon_caption/config.json" "icon_caption/generation_config.json" "icon_caption/model.safetensors") do (
    huggingface-cli download microsoft/OmniParser-v2.0 %%F --local-dir weights
)
if exist weights\icon_caption (
    if not exist weights\icon_caption_florence ren weights\icon_caption icon_caption_florence
)
cd ..
call env_omniparser\Scripts\deactivate.bat 2>nul

if not exist OmniParser\weights\icon_detect\model.pt (
    echo [ERROR] Weights did not finish downloading. Run setup.bat again to resume - downloaded files are skipped.
    pause
    exit /b 1
)
echo done> "%STATE%\weights.ok"
echo Step 4 complete.
exit /b 0
