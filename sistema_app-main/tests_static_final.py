from pathlib import Path

root = Path(__file__).resolve().parent
app = (root / 'app.py').read_text(encoding='utf-8')
templates = {p.name: p.read_text(encoding='utf-8') for p in (root/'templates').glob('*.html')}
all_text = app + '\n'.join(templates.values())

checks = {
    'categoria ALL/PAS': 'ALL/PAS' in all_text,
    'categoria PRIVADO': 'PRIVADO' in all_text,
    'campo Lucro Empresa': 'Lucro Empresa' in all_text and 'company_total_value' in app,
    'PRIVADO zera Lucro Maxsuel': 'max_profit_total": Decimal("0.00")' in app or 'max_profit_total": Decimal("0.00")' in app,
    'nota fiscal restrita': 'can_view_project_invoice' in app and 'invoice_file' in all_text,
    'exclusao projeto': 'project_delete' in app and 'project_delete' in all_text,
    'exclusao projeto mestre': 'project_master_delete' in app and 'project_master_delete' in all_text,
    'anexos': 'class ProjectAttachment' in app and 'attachments' in all_text,
    'comentarios': 'class ProjectComment' in app and 'comments' in all_text,
    'checklist projeto': 'class ProjectChecklistItem' in app and 'project_checklist_add' in app and 'Checklist do Projeto' in all_text,
    'checklist sla': 'due_date = db.Column(db.Date' in app and 'get_checklist_sla' in app and 'Prazo:' in all_text,
    'escopo por escola': 'permitted_school_ids' in app,
    'escopo por disciplina': 'permitted_discipline_ids' in app,
    'projetista sem financeiro global': 'can_see_global_financial_data' in app,
    'dashboard': 'dashboard.html' in templates,
    'financeiro': 'financial_overview.html' in templates,
    'meu financeiro': 'my_financial_overview.html' in templates,
    'projeto form': 'project_form.html' in templates,
    'projeto mestre': 'project_master_detail.html' in templates,
}
failed = [name for name, ok in checks.items() if not ok]
print('STATIC_CHECKS=', len(checks))
if failed:
    print('FAILED:')
    for name in failed:
        print('-', name)
    raise SystemExit(1)
print('ALL_STATIC_CHECKS_OK')
