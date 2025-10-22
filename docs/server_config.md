# 🚀 Deploy CI/CD Pandora Backend

Este documento descreve passo a passo a configuração de deploy automático/manual do projeto **Pandora Backend** usando **GitHub Actions** e **Docker Compose**.

---

## 1️⃣ Configurar servidor para acessar o GitHub (pull do repo)

Objetivo: permitir que o servidor baixe/atualize o código do repositório sem senha.

**Passos:**

1. Gerar uma **chave SSH dedicada** no servidor:

```bash
ssh-keygen -t rsa -b 4096 -C "deploy@pandora-backend" -f ~/.ssh/github_deploy
```

1.2. Adicionar a chave pública ao GitHub como Deploy Key (somente leitura):

- Vá no repositório → `Settings → Deploy Keys → Add deploy key`
- Cole o conteúdo de `~/.ssh/github_deploy.pub`

  1.3. Configurar SSH no servidor para usar a chave:

```bash
echo "
Host github.com
  IdentityFile ~/.ssh/github_deploy
  StrictHostKeyChecking no
" >> ~/.ssh/config
chmod 600 ~/.ssh/config
```

## 2️⃣ Configurar GitHub Actions para acessar o servidor via SSH

Objetivo: permitir que o GitHub Actions execute comandos no servidor, como atualizar o código e subir containers.

**Passos:**

2.1. Gerar uma chave SSH dedicada para GitHub Actions:

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/github_actions
```

2.2. Adicionar a chave pública no servidor:

```bash
cat ~/github_actions.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
chmod 700 ~/.ssh
```

2.3. Criar Secrets no GitHub:

| Nome           | Valor                                    |
| -------------- | ---------------------------------------- |
| SERVER_HOST    | IP do servidor                           |
| SERVER_USER    | Usuário SSH (ex: ubuntu)                 |
| SERVER_SSH_KEY | Conteúdo da chave privada github_actions |

## 3️⃣ Configurar Docker Compose no servidor

3.1. Testar manualmente:

```bash
cd /home/ubuntu/pandora-backend
docker compose -f docker-compose.prod.yml up -d --build
docker ps
```

## 4️⃣ Configurar workflow do GitHub Actions (deploy manual)

Arquivo sugerido: .github/workflows/deploy.yml

```
name: 🚀 Deploy Pandora Backend

# Disparo manual
on:
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          script: |
            set -e
            cd /home/ubuntu/pandora-backend

            echo "→ Atualizando código"
            git fetch origin main
            git reset --hard origin/main

            echo "→ Atualizando containers"
            docker compose -f docker-compose.prod.yml down
            docker compose -f docker-compose.prod.yml up -d --build

            echo "✅ Deploy concluído com sucesso!"
```
