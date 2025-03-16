#!/bin/bash

# Testa se o comando de migração ainda está rodando
if [ "$(ps aux | grep '[p]ython manage.py migrate' | wc -l)" -eq "0" ]; then
  exit 0 # Serviço saudável
else
  exit 1 # Serviço ainda está rodando
fi