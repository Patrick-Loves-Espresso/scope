@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem SCOPE Installation Script for Windows
rem Installs Claude and Codex variants from shared and platform-specific sources.
rem
rem Usage:
rem   install.bat                    Install to the current project
rem   install.bat --user             Install to the user home directory
rem   install.bat C:\path\to\project  Install to a custom target directory

set "SCRIPT_DIR=%~dp0"
set "VERSION=1.1.0"
set "SHARED_SRC=%SCRIPT_DIR%src_shared"
set "CLAUDE_SRC=%SCRIPT_DIR%src_claude"
set "CODEX_SRC=%SCRIPT_DIR%src_codex"

echo.
echo ========================================
echo          SCOPE Installer v%VERSION%
echo ========================================
echo.

set "INSTALL_TYPE=project"
set "INSTALL_DIR=."
set "CLAUDE_DIR=.\.claude"
set "CODEX_DIR=.\plugins\scope"

if /I "%~1"=="--user" (
    set "INSTALL_TYPE=user"
    set "INSTALL_DIR=%USERPROFILE%"
    set "CLAUDE_DIR=%USERPROFILE%\.claude"
    set "CODEX_DIR=%USERPROFILE%\plugins\scope"
    echo Installing to user directory:
) else if not "%~1"=="" (
    set "INSTALL_DIR=%~1"
    set "CLAUDE_DIR=%~1\.claude"
    set "CODEX_DIR=%~1\plugins\scope"
    echo Installing to custom directory:
) else (
    echo Installing to project directory:
)

echo   Claude: "%CLAUDE_DIR%"
echo   Codex:  "%CODEX_DIR%"
echo.

echo Creating Directory Structure
echo.

call :ensure_dir "%CLAUDE_DIR%\commands"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\skills"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\agents"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\workers"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\governance"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\config"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CLAUDE_DIR%\scripts"
if errorlevel 1 goto :install_failed

call :ensure_dir "%CODEX_DIR%\commands"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\skills"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\agents"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\workers"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\governance"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\docs"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\scripts"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\config"
if errorlevel 1 goto :install_failed
call :ensure_dir "%CODEX_DIR%\.codex-plugin"
if errorlevel 1 goto :install_failed

echo   Created "%CLAUDE_DIR%\"
echo   Created "%CODEX_DIR%\"

rem Remove obsolete reviewer transports left by older Scope installations.
call :delete_if_exists "%CLAUDE_DIR%\workers\audit-worker.md"
call :delete_if_exists "%CODEX_DIR%\workers\audit-worker.md"
call :delete_if_exists "%CLAUDE_DIR%\commands\scripts\scope-reviewer-tmux.sh"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\scripts\scope-reviewer-claude-pexpect.py"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\scripts\scope-reviewer-claude-pexpect.py"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\scripts\validate-architecture-contracts.sh"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\scripts\validate-epic-docs.sh"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\scripts\scope-reviewer-tmux.sh"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\scripts\scope-reviewer-claude-pexpect.py"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\scripts\scope-proof-preflight.py"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\scripts\scope-proof-preflight.py"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\config\worker-runtime-policy.yaml"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\config\worker-runtime-policy.yaml"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\implement_tdd.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\commands\implement_tdd.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\docs\epic-workflow.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\governance\agent-lifecycle.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\governance\agent-lifecycle.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\audit_epic\reviewer-gemini.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\commands\audit_epic\reviewer-gemini.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CLAUDE_DIR%\commands\epic_refine\reviewer-architecture-gemini.md"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\commands\epic_refine\reviewer-architecture-gemini.md"
if errorlevel 1 goto :install_failed
for %%R in (reviewer-codex reviewer-claude reviewer-agy reviewer-glm) do (
    call :delete_if_exists "%CLAUDE_DIR%\commands\audit_epic\%%R.md"
    if errorlevel 1 goto :install_failed
    call :delete_if_exists "%CODEX_DIR%\commands\audit_epic\%%R.md"
    if errorlevel 1 goto :install_failed
)
for %%R in (reviewer-architecture-codex reviewer-architecture-claude reviewer-architecture-agy reviewer-architecture-glm) do (
    call :delete_if_exists "%CLAUDE_DIR%\commands\epic_refine\%%R.md"
    if errorlevel 1 goto :install_failed
    call :delete_if_exists "%CODEX_DIR%\commands\epic_refine\%%R.md"
    if errorlevel 1 goto :install_failed
)
for %%F in (system-context architecture adr pdr test-strategy) do (
    call :delete_if_exists "%CLAUDE_DIR%\skills\project-documentation\templates-technical-arc42-c4\epic\%%F.md"
    if errorlevel 1 goto :install_failed
    call :delete_if_exists "%CODEX_DIR%\skills\project-documentation\templates-technical-arc42-c4\epic\%%F.md"
    if errorlevel 1 goto :install_failed
)
call :delete_if_exists "%CLAUDE_DIR%\skills\project-documentation\templates-technical-arc42-c4\epic\acceptance-traceability.yaml"
if errorlevel 1 goto :install_failed
call :delete_if_exists "%CODEX_DIR%\skills\project-documentation\templates-technical-arc42-c4\epic\acceptance-traceability.yaml"
if errorlevel 1 goto :install_failed

echo.
echo Installing Claude Files
echo.

call :copy_overlay "%SHARED_SRC%\commands" "%CLAUDE_DIR%\commands"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\commands" "%CLAUDE_DIR%\commands"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\scripts" "%CLAUDE_DIR%\scripts"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\scripts" "%CLAUDE_DIR%\scripts"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\config" "%CLAUDE_DIR%\config"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\config" "%CLAUDE_DIR%\config"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\skills" "%CLAUDE_DIR%\skills"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\skills" "%CLAUDE_DIR%\skills"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\agents" "%CLAUDE_DIR%\agents"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\agents" "%CLAUDE_DIR%\agents"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\workers" "%CLAUDE_DIR%\workers"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\governance" "%CLAUDE_DIR%\governance"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CLAUDE_SRC%\governance" "%CLAUDE_DIR%\governance"
if errorlevel 1 goto :install_failed

echo   Commands:
call :list_markdown_files "%CLAUDE_DIR%\commands" "/"
echo   Command resources:
call :list_directories "%CLAUDE_DIR%\commands" "/" "/"
echo   Skills:
call :list_directories "%CLAUDE_DIR%\skills" "" ""
echo   Agents:
call :list_markdown_files "%CLAUDE_DIR%\agents" ""
echo   Workers:
call :list_markdown_files "%CLAUDE_DIR%\workers" ""
echo   Governance:
call :list_markdown_files "%CLAUDE_DIR%\governance" ""

echo.
echo Installing Codex Plugin
echo.

call :copy_overlay "%SHARED_SRC%\commands" "%CODEX_DIR%\commands"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\commands" "%CODEX_DIR%\commands"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\skills" "%CODEX_DIR%\skills"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\skills" "%CODEX_DIR%\skills"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\agents" "%CODEX_DIR%\agents"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\agents" "%CODEX_DIR%\agents"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\workers" "%CODEX_DIR%\workers"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\governance" "%CODEX_DIR%\governance"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\governance" "%CODEX_DIR%\governance"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\docs" "%CODEX_DIR%\docs"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\docs" "%CODEX_DIR%\docs"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\scripts" "%CODEX_DIR%\scripts"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\scripts" "%CODEX_DIR%\scripts"
if errorlevel 1 goto :install_failed
call :copy_overlay "%SHARED_SRC%\config" "%CODEX_DIR%\config"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\config" "%CODEX_DIR%\config"
if errorlevel 1 goto :install_failed
call :copy_overlay "%CODEX_SRC%\.codex-plugin" "%CODEX_DIR%\.codex-plugin"
if errorlevel 1 goto :install_failed
call :copy_file_if_exists "%CODEX_SRC%\README.md" "%CODEX_DIR%\README.md"
if errorlevel 1 goto :install_failed
call :copy_file_if_exists "%CODEX_SRC%\.mcp.json" "%CODEX_DIR%\.mcp.json"
if errorlevel 1 goto :install_failed
call :copy_file_if_exists "%SCRIPT_DIR%requirements.txt" "%CLAUDE_DIR%\requirements.txt"
if errorlevel 1 goto :install_failed
call :copy_file_if_exists "%SCRIPT_DIR%requirements.txt" "%CODEX_DIR%\requirements.txt"
if errorlevel 1 goto :install_failed

echo   Plugin root:
if exist "%CODEX_DIR%\README.md" echo     [ok] README.md
if exist "%CODEX_DIR%\.mcp.json" echo     [ok] .mcp.json
if exist "%CODEX_DIR%\.codex-plugin\plugin.json" echo     [ok] .codex-plugin\plugin.json
echo   Commands:
call :list_markdown_files "%CODEX_DIR%\commands" "scope:"
echo   Skills:
call :list_directories "%CODEX_DIR%\skills" "" ""
echo   Agents:
call :list_markdown_files "%CODEX_DIR%\agents" ""
echo   Workers:
call :list_markdown_files "%CODEX_DIR%\workers" ""
echo   Docs:
call :list_markdown_files "%CODEX_DIR%\docs" ""
echo   Scripts:
call :list_all_files "%CODEX_DIR%\scripts"

if /I "%INSTALL_TYPE%"=="project" (
    echo.
    echo Creating Configuration
    echo.

    call :ensure_dir "%INSTALL_DIR%\.scope"
    if errorlevel 1 goto :install_failed

    if exist "%INSTALL_DIR%\.scope\config.yaml" (
        echo   [skip] .scope\config.yaml already exists
    ) else (
        copy /Y "%SHARED_SRC%\commands\config_example.yaml" "%INSTALL_DIR%\.scope\config.yaml" >nul
        if errorlevel 1 goto :install_failed
        echo   [ok] Created .scope\config.yaml from template
        echo.
        echo   Local defaults are active:
        echo     - documentation: .\docs
        echo     - tracking: .\tracking
        echo.
    )
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.

if /I "%INSTALL_TYPE%"=="project" (
    echo Next Steps:
    echo.
    if not "%INSTALL_DIR%"=="." (
        echo 1. Navigate to the project:
        echo    cd /d "%INSTALL_DIR%"
        echo.
        echo 2. Start using SCOPE:
    ) else (
        echo 1. Start using SCOPE:
    )
    echo    Claude: /prd_create, /prd_refine, /prd_breakdown, /epic_refine {epic-id}, /implement {epic-id}
    echo    Codex:  scope:prd_create, scope:prd_refine, scope:prd_breakdown, scope:epic_refine E1, scope:implement E1
    echo.
) else (
    echo Next Steps:
    echo.
    echo 1. In any project, use the installed directories:
    echo    Claude: "%CLAUDE_DIR%"
    echo    Codex:  "%CODEX_DIR%"
    echo.
)

echo Documentation: "%SCRIPT_DIR%docs\scope-architecture.md"
echo.
exit /b 0

:ensure_dir
if exist "%~1\" exit /b 0
mkdir "%~1"
if errorlevel 1 exit /b 1
exit /b 0

:delete_if_exists
if not exist "%~1" exit /b 0
del /F /Q "%~1"
if errorlevel 1 exit /b 1
exit /b 0

:copy_overlay
if not exist "%~1\" exit /b 0
call :ensure_dir "%~2"
if errorlevel 1 exit /b 1
xcopy "%~1\*" "%~2\" /E /H /I /Q /R /Y >nul
if errorlevel 1 exit /b 1
for /R "%~2" %%F in (.DS_Store) do (
    if exist "%%F" del /F /Q "%%F"
    if errorlevel 1 exit /b 1
)
for /D /R "%~2" %%D in (__pycache__ .pytest_cache) do (
    if exist "%%D" rmdir /S /Q "%%D"
    if errorlevel 1 exit /b 1
)
for /R "%~2" %%F in (*.pyc *.pyo) do (
    if exist "%%F" del /F /Q "%%F"
    if errorlevel 1 exit /b 1
)
exit /b 0

:copy_file_if_exists
dir /B /A "%~1" >nul 2>&1
if errorlevel 1 exit /b 0
xcopy "%~1" "%~dp2" /H /Q /R /Y >nul
if errorlevel 1 exit /b 1
exit /b 0

:list_markdown_files
for /F "delims=" %%F in ('dir /B /A-D /ON "%~1\*.md" 2^>nul') do echo     [ok] %~2%%~nF
exit /b 0

:list_directories
for /F "delims=" %%D in ('dir /B /AD /ON "%~1\*" 2^>nul') do echo     [ok] %~2%%D%~3
exit /b 0

:list_all_files
for /F "delims=" %%F in ('dir /B /A-D /ON "%~1\*" 2^>nul') do echo     [ok] %%F
exit /b 0

:install_failed
echo.
echo Installation failed. No successful completion was reported.
exit /b 1
