@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (echo VCVARS_FAILED & exit /b 1)
where cl
cd /d D:\apk-analysis\blutter
python D:\apk-analysis\blutter\blutter.py D:\apk-analysis\wonder514\blutter_in D:\apk-analysis\wonder514\blutter_out
echo BLUTTER_EXIT=%ERRORLEVEL%
