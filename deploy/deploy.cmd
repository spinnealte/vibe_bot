@echo off
rem Deploy stubBot to the server with one command (run from the project folder):
rem     deploy\deploy.cmd root@SERVER_IP
rem
rem Uploads the last commit and runs deploy/install.sh on the server.
rem The first run installs everything, later runs update the bot. See deploy/README.md.
setlocal
rem The installer prints Russian text in UTF-8.
chcp 65001 >nul

if "%~1"=="" (
    echo Usage: deploy\deploy.cmd root@SERVER_IP
    exit /b 1
)
set "SERVER=%~1"
set "ARCHIVE=%TEMP%\stubbot-release.tar.gz"

pushd "%~dp0.."
git diff --quiet HEAD -- . || echo WARNING: uncommitted changes are NOT deployed - only the last commit goes to the server.
git archive --format=tar.gz -o "%ARCHIVE%" HEAD
if errorlevel 1 (
    popd
    echo ERROR: could not pack the code. Is git installed and is this the project folder?
    exit /b 1
)
popd

echo Uploading the code to %SERVER% ...
scp "%ARCHIVE%" %SERVER%:/root/stubbot-release.tar.gz
if errorlevel 1 (
    echo ERROR: upload failed. Check the server address and SSH access.
    exit /b 1
)

ssh -t %SERVER% "rm -rf /root/stubbot-release && mkdir -p /root/stubbot-release && tar -xzf /root/stubbot-release.tar.gz -C /root/stubbot-release && bash /root/stubbot-release/deploy/install.sh /root/stubbot-release"
exit /b %ERRORLEVEL%
