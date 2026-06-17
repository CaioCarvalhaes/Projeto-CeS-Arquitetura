# Gestão Projetos C&S Arquitetura — Versão Final Consolidada

## Credenciais iniciais
- Admin: `admin@empresa.local` / `admin123`
- Gestão: `gestao@empresa.local` / `gestao123`
- Projetista teste: `projetista.teste@empresa.local` / `teste123`

## Como executar no Windows PowerShell
```powershell
cd C:\CAMINHO\DA\PASTA\sistema_final_cs
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\app.py
```

Acesse:
```text
http://127.0.0.1:5000
```

## Principais módulos incluídos
- Dashboard por perfil
- Projetos
- Projetos Mestre
- Meu Financeiro
- Financeiro global
- Cadastros
- Auditoria
- Anexos por projeto e projeto mestre
- Comentários internos por projeto e projeto mestre
- Nota fiscal restrita a Admin/Gestão
- Permissões por escopo
- Categorias ALL/PAS e PRIVADO

## Regra financeira oficial
### ALL/PAS
- Usa cálculo por m²
- Alimenta Valor Giulia
- Alimenta Valor Projetistas
- Alimenta Lucro Maxsuel
- Lucro Empresa fica zerado

### PRIVADO
- Usa valor manual
- Não distribui para Giulia
- Não distribui para projetista
- Não alimenta Lucro Maxsuel
- Alimenta Lucro Empresa

## Regra de privacidade do projetista
Projetista vê somente:
- projetos vinculados a ele
- meu valor vinculado
- anexos/comentários permitidos

Projetista não vê:
- nota total global
- Valor Giulia
- Lucro Maxsuel
- Lucro Empresa
- nota fiscal
- financeiro global

## Validação executada nesta entrega
- Compilação sintática do `app.py`
- Checagem estática de presença dos módulos críticos
- Verificação estática de templates principais
- Verificação de rotas/funções críticas existentes no código

Observação: a validação dinâmica com servidor Flask depende da instalação das bibliotecas em `requirements.txt` no ambiente local.
