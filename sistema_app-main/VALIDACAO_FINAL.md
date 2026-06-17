# Validação Final — Gestão Projetos C&S Arquitetura

## Checagens executadas

### Sintaxe
- `app.py` compilado com sucesso via `python -S -m py_compile app.py`

### Funcionalidades verificadas por checagem estática
- Categoria ALL/PAS
- Categoria PRIVADO
- Campo Lucro Empresa
- PRIVADO zerando Lucro Maxsuel
- Nota fiscal restrita
- Exclusão de Projeto
- Exclusão de Projeto Mestre
- Anexos
- Comentários internos
- Escopo por escola
- Escopo por disciplina
- Bloqueio de financeiro global para Projetista
- Dashboard
- Financeiro
- Meu Financeiro
- Formulário de Projeto
- Detalhe de Projeto Mestre

Resultado: `ALL_STATIC_CHECKS_OK`

## Checklist manual recomendado após baixar
1. Entrar como Admin.
2. Criar projeto ALL/PAS e conferir cálculo por m².
3. Criar projeto PRIVADO e conferir Lucro Empresa.
4. Confirmar que PRIVADO não alimenta Lucro Maxsuel.
5. Entrar como Projetista e confirmar que não vê valores globais.
6. Testar upload/download de anexo.
7. Testar nota fiscal com Admin/Gestão.
8. Confirmar que Projetista não vê nota fiscal.
9. Testar comentários.
10. Testar exclusão de projeto e projeto mestre.
