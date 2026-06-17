# sistema_app

Sistema web em Flask para gestao de projetos, financeiro operacional, custos da empresa e acompanhamento de pendencias.

## Principais modulos

- Dashboard operacional e financeiro
- Cadastro de projetos e projetos mestre
- Escolas, disciplinas, projetistas e usuarios
- Tabela de precificacao por m2
- Controle de custos da empresa
- Central de pendencias
- Checklist por projeto com SLA de prazo
- Anexos, comentarios e historico de projetos

## Stack

- Python 3.11+
- Flask
- Flask-SQLAlchemy
- SQLite
- Bootstrap 5

## Estrutura

```text
sistema_app/
|- app.py
|- requirements.txt
|- templates/
|- README.md
|- README_FINAL.md
|- VALIDACAO_FINAL.md
```

## Instalacao

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

### Linux/macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Acesso local

Abra no navegador:

```text
http://127.0.0.1:5000
```

## Banco de dados

O projeto usa SQLite local em `database.db`.

Esse arquivo nao deve ser publicado no GitHub. O `.gitignore` ja foi preparado para isso.

## Arquivos que nao devem subir

O repositorio foi preparado para ignorar:

- `database.db`
- `uploads/`
- `__pycache__/`
- `.venv/`
- arquivos de ambiente local

## Usuarios iniciais

O seed da aplicacao cria usuarios locais de exemplo quando o banco ainda esta vazio:

- `admin@empresa.local`
- `gestao@empresa.local`

As senhas ficam definidas no seed de `app.py` e devem ser trocadas em ambiente real.

## Funcionalidades importantes

### Projetos

- cadastro de projeto por disciplina
- vinculo com projeto mestre
- calculo automatico dos valores do projeto
- historico, comentarios e anexos

### Financeiro

- faturamento por periodo
- custos fixos, variaveis e impostos
- imposto automatico de 6% sobre projetos
- visao consolidada de resultado

### Operacao

- central de pendencias
- checklist por projeto
- SLA de checklist com status:
  - sem prazo
  - no prazo
  - proximo do prazo
  - vence hoje
  - atrasado

## Validacao rapida

Para validar sintaxe e checks estaticos:

```powershell
python -m py_compile app.py tests_static_final.py
python tests_static_final.py
```

## Publicacao no GitHub

Depois de criar o repositorio remoto com nome `sistema_app`, rode:

```powershell
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <URL_DO_REPOSITORIO>
git push -u origin main
```

## Observacoes

- O projeto ainda usa seed local e SQLite, entao ele esta pronto para desenvolvimento e validacao interna.
- Para producao, vale separar configuracoes por ambiente e revisar segredo da aplicacao.
