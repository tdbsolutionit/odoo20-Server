@echo off
cd /d "%~dp0"
echo ========================================================
echo  KHOI DONG ODOO 20 SERVER (DEV MODE)
echo  Truy cap trinh duyet: http://localhost:8070
echo  Nhan Ctrl + C de dung server bat ky luc nao
echo ========================================================
".venv\Scripts\python.exe" "odoo-bin" -c "config\odoo.conf" %*
pause
