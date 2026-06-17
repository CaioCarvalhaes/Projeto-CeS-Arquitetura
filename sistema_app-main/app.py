from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import wraps
from typing import Optional
from collections import defaultdict

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory,
    abort,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "troque-esta-chave-em-producao")

db = SQLAlchemy(app)

EXECUTOR_TYPES = ["GIULIA", "DESIGNER"]
PROJECT_CATEGORIES = ["ALL/PAS", "PRIVADO"]
PAYMENT_STATUSES = ["EM ABERTO", "PARCIAL", "PAGO", "ATRASADO"]
OPERATIONAL_STATUSES = [
    "CADASTRADO",
    "AGUARDANDO INÍCIO",
    "EM ANDAMENTO",
    "EM REVISÃO",
    "APROVADO",
    "ENTREGUE",
    "CONCLUÍDO",
    "ATRASADO",
]
USER_ROLES = ["ADMIN", "GESTAO", "FINANCEIRO", "PROJETISTA"]
APP_NAME = "Gestão Projetos C&S Arquitetura"
COMPETENCE_TYPES = ["delivery", "invoice", "payment"]
COMPETENCE_LABELS = {"delivery": "Entrega", "invoice": "Faturamento", "payment": "Pagamento"}
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "dwg", "dxf", "png", "jpg", "jpeg", "webp", "gif", "doc", "docx", "xls", "xlsx", "txt", "zip", "rar"}
COMMENT_TYPES = ["GERAL", "GESTAO", "TECNICO", "PENDENCIA", "REVISAO", "RESPOSTA"]
COST_TYPES = ["FIXO", "VARIAVEL", "IMPOSTO"]
COST_CATEGORIES = ["LUZ", "ENERGIA", "ALUGUEL", "CONDOMINIO", "INTERNET", "FOLHA", "SOFTWARE", "IMPOSTO", "TAXA", "OUTROS"]
PROJECT_TAX_RATE = Decimal("0.06")


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="GESTAO")
    active = db.Column(db.Boolean, default=True, nullable=False)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=True)

    can_view_all_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_view_financial_dashboard = db.Column(db.Boolean, default=False, nullable=False)
    can_manage_master_data = db.Column(db.Boolean, default=False, nullable=False)
    can_manage_users = db.Column(db.Boolean, default=False, nullable=False)
    can_create_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_edit_own_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_edit_assigned_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_edit_all_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_delete_projects = db.Column(db.Boolean, default=False, nullable=False)
    can_export_projects = db.Column(db.Boolean, default=False, nullable=False)
    permitted_school_ids = db.Column(db.Text, nullable=True)
    permitted_discipline_ids = db.Column(db.Text, nullable=True)
    only_participating_projects = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    designer = db.relationship("Designer", backref="linked_users")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"

    def can_access_all_projects(self) -> bool:
        return self.is_admin or self.can_view_all_projects

    def can_manage_catalogs(self) -> bool:
        return self.is_admin or self.can_manage_master_data

    def can_manage_system_users(self) -> bool:
        return self.is_admin or self.can_manage_users

    def can_create_new_projects(self) -> bool:
        return self.is_admin or self.can_create_projects or self.role in {"GESTAO", "FINANCEIRO"}

    def can_edit_own_created_projects(self) -> bool:
        return self.is_admin or self.can_edit_own_projects

    def can_edit_projects_assigned_to_me(self) -> bool:
        return self.is_admin or self.can_edit_assigned_projects

    def can_edit_any_project(self) -> bool:
        return self.is_admin or self.can_edit_all_projects

    def can_remove_projects(self) -> bool:
        return self.is_admin or self.can_delete_projects

    def can_export_any_projects(self) -> bool:
        return self.is_admin or self.can_export_projects

    def can_see_financial_dashboard(self) -> bool:
        return self.is_admin or self.can_view_financial_dashboard

    def can_see_global_financial_data(self) -> bool:
        return self.role in {"ADMIN", "GESTAO", "FINANCEIRO"} and self.can_see_financial_dashboard()

    def can_view_project_invoice(self) -> bool:
        return self.role in {"ADMIN", "GESTAO"}

    def can_view_company_costs(self) -> bool:
        return self.role in {"ADMIN", "GESTAO", "FINANCEIRO"} and self.can_see_financial_dashboard()

    def can_manage_company_costs(self) -> bool:
        return self.role in {"ADMIN", "GESTAO"}

    def get_permitted_school_ids(self) -> set[int]:
        return parse_id_csv(self.permitted_school_ids)

    def get_permitted_discipline_ids(self) -> set[int]:
        return parse_id_csv(self.permitted_discipline_ids)

    def has_scope_school_restriction(self) -> bool:
        return bool(self.get_permitted_school_ids())

    def has_scope_discipline_restriction(self) -> bool:
        return bool(self.get_permitted_discipline_ids())


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(2), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)


class Discipline(db.Model):
    __tablename__ = "disciplines"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    active = db.Column(db.Boolean, default=True, nullable=False)


class Designer(db.Model):
    __tablename__ = "designers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    email = db.Column(db.String(150), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)


class PricingRule(db.Model):
    __tablename__ = "pricing_rules"

    id = db.Column(db.Integer, primary_key=True)
    discipline_id = db.Column(db.Integer, db.ForeignKey("disciplines.id"), nullable=False)
    total_invoice_per_m2 = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    max_profit_per_m2 = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    giulia_solo_per_m2 = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    designer_per_m2 = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    giulia_when_designer_per_m2 = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    discipline = db.relationship("Discipline", backref="pricing_rules")


class CompanyCost(db.Model):
    __tablename__ = "company_costs"

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="OUTROS")
    cost_type = db.Column(db.String(20), nullable=False, default="FIXO")
    value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    competence_month = db.Column(db.String(7), nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    created_by_user = db.relationship("User")


class ProjectMaster(db.Model):
    __tablename__ = "project_masters"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    client_name = db.Column(db.String(150), nullable=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    macro_status = db.Column(db.String(30), nullable=False, default="CADASTRADO")
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    school = db.relationship("School", backref="project_masters")
    assigned_user = db.relationship("User", foreign_keys=[assigned_user_id], backref="assigned_project_masters")
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id], backref="created_project_masters")


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    project_name = db.Column(db.String(150), nullable=False)
    project_category = db.Column(db.String(20), nullable=False, default="ALL/PAS")
    master_id = db.Column(db.Integer, db.ForeignKey("project_masters.id"), nullable=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    discipline_id = db.Column(db.Integer, db.ForeignKey("disciplines.id"), nullable=False)
    area_m2 = db.Column(db.Numeric(12, 2), nullable=False)
    executor_type = db.Column(db.String(20), nullable=False)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=True)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    deadline = db.Column(db.Date, nullable=True)
    delivery_date = db.Column(db.Date, nullable=True)
    invoice_date = db.Column(db.Date, nullable=True)
    pending = db.Column(db.String(150), nullable=True)
    revision = db.Column(db.String(150), nullable=True)
    approved = db.Column(db.String(150), nullable=True)
    payment_date = db.Column(db.Date, nullable=True)
    payment_due_date = db.Column(db.Date, nullable=True)
    payment_status = db.Column(db.String(30), nullable=False, default="EM ABERTO")
    operational_status = db.Column(db.String(30), nullable=False, default="CADASTRADO")
    notes = db.Column(db.Text, nullable=True)

    invoice_per_m2_snapshot = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    max_profit_per_m2_snapshot = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    giulia_per_m2_snapshot = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    designer_per_m2_snapshot = db.Column(db.Numeric(10, 2), nullable=False, default=0)

    total_invoice_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    giulia_total_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    designer_total_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    max_profit_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    manual_total_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    company_total_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    invoice_file_original_filename = db.Column(db.String(255), nullable=True)
    invoice_file_stored_filename = db.Column(db.String(255), nullable=True)
    invoice_file_ext = db.Column(db.String(20), nullable=True)
    invoice_file_mime_type = db.Column(db.String(120), nullable=True)
    invoice_file_size = db.Column(db.Integer, nullable=False, default=0)
    invoice_file_uploaded_at = db.Column(db.DateTime, nullable=True)
    invoice_file_uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    school = db.relationship("School", backref="projects")
    discipline = db.relationship("Discipline", backref="projects")
    designer = db.relationship("Designer", backref="projects")
    assigned_user = db.relationship("User", foreign_keys=[assigned_user_id], backref="assigned_projects")
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id], backref="created_projects")
    invoice_file_uploaded_by_user = db.relationship("User", foreign_keys=[invoice_file_uploaded_by_user_id])
    master = db.relationship("ProjectMaster", backref="items")


class ProjectHistory(db.Model):
    __tablename__ = "project_history"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    project = db.relationship("Project", backref="history_entries")
    user = db.relationship("User")




class ProjectAttachment(db.Model):
    __tablename__ = "project_attachments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    master_id = db.Column(db.Integer, db.ForeignKey("project_masters.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    file_ext = db.Column(db.String(20), nullable=True)
    mime_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    project = db.relationship("Project", backref="attachments")
    master = db.relationship("ProjectMaster", backref="attachments")
    user = db.relationship("User")


class ProjectComment(db.Model):
    __tablename__ = "project_comments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    master_id = db.Column(db.Integer, db.ForeignKey("project_masters.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    comment_type = db.Column(db.String(30), nullable=False, default="GERAL")
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    project = db.relationship("Project", backref="comments")
    master = db.relationship("ProjectMaster", backref="comments")
    user = db.relationship("User")


class ProjectChecklistItem(db.Model):
    __tablename__ = "project_checklist_items"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    completed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = db.relationship("Project", backref="checklist_items")
    assigned_user = db.relationship("User", foreign_keys=[assigned_user_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    completed_by_user = db.relationship("User", foreign_keys=[completed_by_user_id])


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")

# ----------------------------
# Helpers
# ----------------------------
def to_decimal(value: object, default: str = "0.00") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)


TWOPLACES = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def allowed_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ATTACHMENT_EXTENSIONS


def file_size_human(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size or 0)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def parse_id_csv(value: str | None) -> set[int]:
    ids: set[int] = set()
    if not value:
        return ids
    for part in str(value).split(','):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def ids_to_csv(values) -> str:
    ints = sorted({int(v) for v in values if str(v).strip().isdigit()})
    return ','.join(str(v) for v in ints)


def attachment_delete_allowed(user: User, attachment: ProjectAttachment) -> bool:
    return user.can_manage_catalogs() or user.can_edit_any_project() or user.is_admin


def comment_delete_allowed(user: User, comment: ProjectComment) -> bool:
    return user.can_manage_catalogs() or user.can_edit_any_project() or user.is_admin


def user_can_comment_master(user: User, master: ProjectMaster) -> bool:
    return master_visible_to_user(user, master)


def user_can_comment_project(user: User, project: Project) -> bool:
    return user_can_access_project(user, project)


def checklist_delete_allowed(user: User, item: ProjectChecklistItem) -> bool:
    if user.can_manage_catalogs() or user.can_edit_any_project() or user.is_admin:
        return True
    if item.created_by_user_id and item.created_by_user_id == user.id:
        return True
    return user_can_edit_project(user, item.project)


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def fmt_date(value: Optional[date]) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def calculate_private_project_values(manual_total_value: Decimal, area_m2: Decimal = Decimal("0.00")) -> dict[str, Decimal]:
    total_invoice = q(manual_total_value)
    company_total = total_invoice
    max_profit_per_m2 = Decimal("0.00")
    return {
        "invoice_per_m2": Decimal("0.00"),
        "max_profit_per_m2": max_profit_per_m2,
        "giulia_per_m2": Decimal("0.00"),
        "designer_per_m2": Decimal("0.00"),
        "total_invoice": total_invoice,
        "giulia_total": Decimal("0.00"),
        "designer_total": Decimal("0.00"),
        "max_profit_total": Decimal("0.00"),
        "manual_total": total_invoice,
        "company_total": company_total,
    }


def calculate_project_values(area_m2: Decimal, rule: PricingRule, executor_type: str) -> dict[str, Decimal]:
    invoice_per_m2 = to_decimal(rule.total_invoice_per_m2)

    if executor_type == "GIULIA":
        giulia_per_m2 = to_decimal(rule.giulia_solo_per_m2)
        designer_per_m2 = Decimal("0.00")
    else:
        giulia_per_m2 = to_decimal(rule.giulia_when_designer_per_m2)
        designer_per_m2 = to_decimal(rule.designer_per_m2)

    total_invoice = q(area_m2 * invoice_per_m2)
    giulia_total = q(area_m2 * giulia_per_m2)
    designer_total = q(area_m2 * designer_per_m2)

    # Lucro do Maxsuel = residual exato da nota após Giulia e projetista.
    max_profit_total = q(total_invoice - giulia_total - designer_total)
    max_profit_per_m2 = q((max_profit_total / area_m2) if area_m2 > 0 else Decimal("0.00"))

    return {
        "invoice_per_m2": q(invoice_per_m2),
        "max_profit_per_m2": max_profit_per_m2,
        "giulia_per_m2": q(giulia_per_m2),
        "designer_per_m2": q(designer_per_m2),
        "total_invoice": total_invoice,
        "giulia_total": giulia_total,
        "designer_total": designer_total,
        "max_profit_total": max_profit_total,
    }


def calculate_project_snapshot_values(project: Project) -> dict[str, Decimal]:
    category = (project.project_category or "ALL/PAS").strip().upper()
    area_m2 = q(to_decimal(project.area_m2))

    if category == "PRIVADO":
        return calculate_private_project_values(to_decimal(project.manual_total_value), area_m2)

    invoice_per_m2 = q(to_decimal(project.invoice_per_m2_snapshot))
    giulia_per_m2 = q(to_decimal(project.giulia_per_m2_snapshot))
    designer_per_m2 = q(to_decimal(project.designer_per_m2_snapshot))
    total_invoice = q(area_m2 * invoice_per_m2)
    giulia_total = q(area_m2 * giulia_per_m2)
    designer_total = q(area_m2 * designer_per_m2)
    max_profit_total = q(total_invoice - giulia_total - designer_total)
    max_profit_per_m2 = q((max_profit_total / area_m2) if area_m2 > 0 else Decimal("0.00"))

    return {
        "invoice_per_m2": invoice_per_m2,
        "max_profit_per_m2": max_profit_per_m2,
        "giulia_per_m2": giulia_per_m2,
        "designer_per_m2": designer_per_m2,
        "total_invoice": total_invoice,
        "giulia_total": giulia_total,
        "designer_total": designer_total,
        "max_profit_total": max_profit_total,
        "manual_total": q(to_decimal(project.manual_total_value)) if category == "PRIVADO" else Decimal("0.00"),
        "company_total": q(to_decimal(project.manual_total_value)) if category == "PRIVADO" else Decimal("0.00"),
    }


def derive_rule_profit_per_m2(total_invoice_per_m2: Decimal, giulia_per_m2: Decimal, designer_per_m2: Decimal) -> Decimal:
    total_invoice = q(to_decimal(total_invoice_per_m2))
    giulia = q(to_decimal(giulia_per_m2))
    designer = q(to_decimal(designer_per_m2))
    return q(total_invoice - giulia - designer)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not g.user:
            flash("Faça login para acessar o sistema.", "warning")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapper


def roles_required(*allowed_roles: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not g.user:
                flash("Faça login para acessar o sistema.", "warning")
                return redirect(url_for("login", next=request.path))
            if g.user.role not in allowed_roles:
                flash("Você não tem permissão para esta ação.", "danger")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def permissions_required(checker_name: str, error_message: str = "Você não tem permissão para esta ação."):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not g.user:
                flash("Faça login para acessar o sistema.", "warning")
                return redirect(url_for("login", next=request.path))
            checker = getattr(g.user, checker_name)
            allowed = checker() if callable(checker) else bool(checker)
            if not allowed:
                flash(error_message, "danger")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def add_history(project: Project, action: str, description: str) -> None:
    db.session.add(
        ProjectHistory(
            project_id=project.id,
            user_id=g.user.id if getattr(g, "user", None) else None,
            action=action,
            description=description,
        )
    )


def add_audit(entity_type: str, entity_id: Optional[int], action: str, description: str) -> None:
    db.session.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            description=description,
            user_id=g.user.id if getattr(g, "user", None) else None,
        )
    )


def month_bounds(month_str: str | None) -> tuple[date, date, str]:
    if month_str:
        try:
            start = datetime.strptime(month_str, "%Y-%m").date().replace(day=1)
        except ValueError:
            start = date.today().replace(day=1)
    else:
        start = date.today().replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1)
    else:
        end = start.replace(month=start.month + 1, day=1)
    return start, end, start.strftime("%Y-%m")


def get_competence_date(project: Project, competence: str) -> date:
    if competence == "payment":
        return project.payment_date or project.payment_due_date or project.created_at.date()
    if competence == "invoice":
        return project.invoice_date or project.delivery_date or project.created_at.date()
    return project.delivery_date or project.deadline or project.created_at.date()


def project_reference_date(project: Project) -> date:
    return get_competence_date(project, "delivery")


def normalize_competence(value: str | None) -> str:
    if value in COMPETENCE_TYPES:
        return value
    return "delivery"


def normalize_competence_month(value: str | None) -> str:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            pass
    return date.today().strftime("%Y-%m")


def filter_projects_by_month(projects: list[Project], month_str: str | None, competence: str = "delivery") -> tuple[list[Project], str]:
    start, end, normalized = month_bounds(month_str)
    competence = normalize_competence(competence)
    return [project for project in projects if start <= get_competence_date(project, competence) < end], normalized


def get_visible_company_costs_query(user: User):
    if not user.can_view_company_costs():
        return CompanyCost.query.filter(text("1=0"))
    return CompanyCost.query


def aggregate_payment_groups(projects: list[Project]) -> list[dict[str, object]]:
    rows = []
    for status in PAYMENT_STATUSES:
        matching = [project for project in projects if project.payment_status == status]
        rows.append({
            "status": status,
            "projects": len(matching),
            "invoice": q(sum((to_decimal(project.total_invoice_value) for project in matching), Decimal("0.00"))),
            "designer": q(sum((to_decimal(project.designer_total_value) for project in matching), Decimal("0.00"))),
            "profit": q(sum((to_decimal(project.max_profit_total) for project in matching), Decimal("0.00"))),
            "company": q(sum((to_decimal(project.company_total_value) for project in matching), Decimal("0.00"))),
        })
    return rows


def aggregate_competence_totals(projects: list[Project]) -> dict[str, Decimal | int]:
    total_projects = len(projects)
    invoice = q(sum((to_decimal(project.total_invoice_value) for project in projects), Decimal("0.00")))
    giulia = q(sum((to_decimal(project.giulia_total_value) for project in projects), Decimal("0.00")))
    designer = q(sum((to_decimal(project.designer_total_value) for project in projects), Decimal("0.00")))
    profit = q(sum((to_decimal(project.max_profit_total) for project in projects), Decimal("0.00")))
    company = q(sum((to_decimal(project.company_total_value) for project in projects if (project.project_category or "ALL/PAS").upper() == "PRIVADO"), Decimal("0.00")))
    paid_projects = sum(1 for project in projects if project.payment_status == "PAGO")
    pending_projects = sum(1 for project in projects if project.payment_status in {"EM ABERTO", "PARCIAL"})
    overdue_projects = sum(1 for project in projects if project.payment_status == "ATRASADO")
    average_ticket = q((invoice / total_projects) if total_projects else Decimal("0.00"))
    return {
        "projects": total_projects,
        "invoice": invoice,
        "giulia": giulia,
        "designer": designer,
        "profit": profit,
        "company": company,
        "paid_projects": paid_projects,
        "pending_projects": pending_projects,
        "overdue_projects": overdue_projects,
        "average_ticket": average_ticket,
    }


def calculate_project_tax(projects: list[Project], tax_rate: Decimal = PROJECT_TAX_RATE) -> Decimal:
    invoice = q(sum((to_decimal(project.total_invoice_value) for project in projects), Decimal("0.00")))
    return q(invoice * to_decimal(tax_rate))


def build_cost_summary(projects: list[Project], month_str: str | None) -> dict[str, object]:
    normalized_month = normalize_competence_month(month_str)
    costs = (
        CompanyCost.query
        .filter_by(competence_month=normalized_month, active=True)
        .order_by(CompanyCost.category.asc(), CompanyCost.description.asc())
        .all()
    )
    fixed_total = Decimal("0.00")
    variable_total = Decimal("0.00")
    automatic_tax_total = calculate_project_tax(projects)
    manual_tax_total = Decimal("0.00")
    category_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for cost in costs:
        value = q(to_decimal(cost.value))
        category_totals[cost.category] += value
        if cost.cost_type == "FIXO":
            fixed_total += value
        elif cost.cost_type == "VARIAVEL":
            variable_total += value
        else:
            manual_tax_total += value

    tax_total = q(automatic_tax_total + manual_tax_total)
    manual_total = q(fixed_total + variable_total)
    total_costs = q(manual_total + tax_total)
    invoice_total = q(sum((to_decimal(project.total_invoice_value) for project in projects), Decimal("0.00")))
    net_result = q(invoice_total - total_costs)

    return {
        "month": normalized_month,
        "costs": costs,
        "fixed_total": q(fixed_total),
        "variable_total": q(variable_total),
        "automatic_tax_total": q(automatic_tax_total),
        "manual_tax_total": q(manual_tax_total),
        "tax_total": q(tax_total),
        "manual_total": manual_total,
        "total_costs": total_costs,
        "net_result": net_result,
        "tax_rate": PROJECT_TAX_RATE,
        "category_totals": [
            {"label": label, "total": q(total)}
            for label, total in sorted(category_totals.items(), key=lambda item: item[0])
        ],
    }


def get_project_alerts(project: Project) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    today = date.today()
    if project.deadline:
        if project.deadline < today and project.operational_status not in {"CONCLUÍDO", "ENTREGUE", "APROVADO"}:
            alerts.append({"label": "Prazo atrasado", "class": "badge-danger"})
        elif today <= project.deadline <= today + timedelta(days=7):
            alerts.append({"label": "Prazo em 7 dias", "class": "badge-warning"})
    due_date = project.payment_due_date
    if due_date and project.payment_status != "PAGO":
        if due_date < today:
            alerts.append({"label": "Pagamento vencido", "class": "badge-danger"})
        elif today <= due_date <= today + timedelta(days=7):
            alerts.append({"label": "Pagamento em 7 dias", "class": "badge-warning"})
    pending_checklist = [item for item in project.checklist_items if not item.is_done]
    overdue_checklist = [item for item in pending_checklist if item.due_date and item.due_date < today]
    due_today_checklist = [item for item in pending_checklist if item.due_date == today]
    upcoming_checklist = [item for item in pending_checklist if item.due_date and today < item.due_date <= today + timedelta(days=3)]
    if overdue_checklist:
        alerts.append({"label": f"Checklist atrasada ({len(overdue_checklist)})", "class": "badge-danger"})
    elif due_today_checklist:
        alerts.append({"label": f"Checklist vence hoje ({len(due_today_checklist)})", "class": "badge-warning"})
    elif upcoming_checklist:
        alerts.append({"label": f"Checklist em 3 dias ({len(upcoming_checklist)})", "class": "badge-warning"})
    elif pending_checklist:
        alerts.append({"label": f"Checklist pendente ({len(pending_checklist)})", "class": "badge-primary"})
    return alerts


def get_checklist_sla(item: ProjectChecklistItem) -> dict[str, str]:
    if item.is_done:
        return {"label": "Concluído", "class": "badge-success"}
    if not item.due_date:
        return {"label": "Sem prazo", "class": "badge-neutral"}
    today = date.today()
    if item.due_date < today:
        return {"label": "Atrasado", "class": "badge-danger"}
    if item.due_date == today:
        return {"label": "Vence hoje", "class": "badge-warning"}
    if item.due_date <= today + timedelta(days=3):
        return {"label": "Próximo do prazo", "class": "badge-primary"}
    return {"label": "No prazo", "class": "badge-success"}


def build_critical_projects(projects: list[Project], limit: int = 8) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for project in projects:
        alerts = get_project_alerts(project)
        if alerts:
            rows.append({
                "project": project,
                "alerts": alerts,
                "severity": 0 if any(alert["class"] == "badge-danger" for alert in alerts) else 1,
                "reference_date": project.deadline or project.payment_due_date or project.created_at.date(),
            })
    return sorted(rows, key=lambda item: (item["severity"], item["reference_date"]))[:limit]


def project_has_issue(project: Project, issue_type: str | None = None) -> bool:
    normalized = (issue_type or "").strip().upper()
    has_pending = bool((project.pending or "").strip())
    has_revision = bool((project.revision or "").strip())
    has_approval = bool((project.approved or "").strip())
    has_checklist = any(not item.is_done for item in project.checklist_items)
    if normalized == "PENDENCIA":
        return has_pending
    if normalized == "REVISAO":
        return has_revision
    if normalized == "APROVACAO":
        return has_approval
    if normalized == "CHECKLIST":
        return has_checklist
    return has_pending or has_revision or has_approval or has_checklist


def build_issue_center_rows(projects: list[Project], issue_type: str | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for project in projects:
        if not project_has_issue(project, issue_type):
            continue
        checklist_items = [
            item for item in sorted(project.checklist_items, key=lambda checklist_item: (checklist_item.position, checklist_item.created_at))
            if not item.is_done
        ]
        overdue_checklist = sum(1 for item in checklist_items if item.due_date and item.due_date < date.today())
        rows.append({
            "project": project,
            "has_pending": bool((project.pending or "").strip()),
            "has_revision": bool((project.revision or "").strip()),
            "has_approval": bool((project.approved or "").strip()),
            "has_checklist": bool(checklist_items),
            "checklist_items": checklist_items,
            "checklist_count": len(checklist_items),
            "checklist_overdue_count": overdue_checklist,
            "alerts": get_project_alerts(project),
        })
    return sorted(rows, key=lambda row: (0 if row["checklist_overdue_count"] else 1, row["project"].deadline or date.max, row["project"].created_at.date()))


def summarize_issue_center(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        "projects": len(rows),
        "pending": sum(1 for row in rows if row["has_pending"]),
        "revision": sum(1 for row in rows if row["has_revision"]),
        "approval": sum(1 for row in rows if row["has_approval"]),
        "checklist": sum(1 for row in rows if row["has_checklist"]),
        "critical": sum(1 for row in rows if any(alert["class"] == "badge-danger" for alert in row["alerts"])),
    }


def build_cashflow_summary(projects: list[Project], cost_summary: dict[str, object]) -> dict[str, Decimal]:
    gross_inflow = q(sum((to_decimal(project.total_invoice_value) for project in projects), Decimal("0.00")))
    received_inflow = q(sum((to_decimal(project.total_invoice_value) for project in projects if project.payment_status == "PAGO"), Decimal("0.00")))
    open_inflow = q(gross_inflow - received_inflow)
    operational_outflow = q(to_decimal(cost_summary["total_costs"]))
    people_outflow = q(sum((to_decimal(project.giulia_total_value) + to_decimal(project.designer_total_value) for project in projects), Decimal("0.00")))
    total_outflow = q(operational_outflow + people_outflow)
    projected_balance = q(received_inflow - total_outflow)
    return {
        "gross_inflow": gross_inflow,
        "received_inflow": received_inflow,
        "open_inflow": open_inflow,
        "operational_outflow": operational_outflow,
        "people_outflow": people_outflow,
        "total_outflow": total_outflow,
        "projected_balance": projected_balance,
    }


def build_dre_summary(totals: dict[str, Decimal | int], cost_summary: dict[str, object]) -> dict[str, Decimal]:
    gross_revenue = q(to_decimal(totals["invoice"]))
    giulia = q(to_decimal(totals["giulia"]))
    designers = q(to_decimal(totals["designer"]))
    automatic_tax = q(to_decimal(cost_summary["automatic_tax_total"]))
    manual_tax = q(to_decimal(cost_summary["manual_tax_total"]))
    operational_costs = q(to_decimal(cost_summary["fixed_total"]) + to_decimal(cost_summary["variable_total"]))
    net_service_margin = q(gross_revenue - giulia - designers)
    total_deductions = q(giulia + designers + automatic_tax + manual_tax + operational_costs)
    net_result = q(gross_revenue - total_deductions)
    return {
        "gross_revenue": gross_revenue,
        "giulia": giulia,
        "designers": designers,
        "automatic_tax": automatic_tax,
        "manual_tax": manual_tax,
        "operational_costs": operational_costs,
        "net_service_margin": net_service_margin,
        "total_deductions": total_deductions,
        "net_result": net_result,
    }


def build_user_financial_summary(projects: list[Project], selected_user: User | None = None) -> list[dict[str, object]]:
    rows = []
    users = User.query.filter_by(active=True).order_by(User.name.asc()).all()
    for user in users:
        user_projects = []
        total_value = Decimal("0.00")
        for project in projects:
            value = get_user_project_value(user, project)
            if value > 0:
                user_projects.append(project)
                total_value += value
        if selected_user and user.id != selected_user.id:
            continue
        if selected_user or user_projects:
            rows.append({
                "label": user.name,
                "user": user,
                "projects": len(user_projects),
                "total": q(total_value),
                "paid": sum(1 for project in user_projects if project.payment_status == "PAGO"),
                "pending": sum(1 for project in user_projects if project.payment_status in {"EM ABERTO", "PARCIAL"}),
                "overdue": sum(1 for project in user_projects if project.payment_status == "ATRASADO"),
                "items": [(project, get_user_project_value(user, project)) for project in user_projects],
            })
    return sorted(rows, key=lambda item: (item["total"], item["projects"]), reverse=True)


def build_giulia_financial_summary(projects: list[Project]) -> dict[str, object]:
    direct_projects = [project for project in projects if project.executor_type == "GIULIA" and to_decimal(project.giulia_total_value) > 0]
    indirect_projects = [project for project in projects if project.executor_type == "DESIGNER" and to_decimal(project.giulia_total_value) > 0]
    total_value = q(sum((to_decimal(project.giulia_total_value) for project in projects), Decimal("0.00")))
    return {
        "total": total_value,
        "direct_projects": len(direct_projects),
        "indirect_projects": len(indirect_projects),
        "pending": sum(1 for project in projects if to_decimal(project.giulia_total_value) > 0 and project.payment_status in {"EM ABERTO", "PARCIAL", "ATRASADO"}),
    }


def build_category_summary(projects: list[Project], *, use_personal_values: bool = False, user: User | None = None) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for project in projects:
        category = (project.project_category or "ALL/PAS").strip().upper()
        bucket = rows.setdefault(category, {
            "label": category,
            "projects": 0,
            "invoice": Decimal("0.00"),
            "company": Decimal("0.00"),
            "my_value": Decimal("0.00"),
        })
        bucket["projects"] += 1
        bucket["invoice"] = q(to_decimal(bucket["invoice"]) + to_decimal(project.total_invoice_value))
        bucket["company"] = q(to_decimal(bucket["company"]) + to_decimal(project.company_total_value))
        if use_personal_values and user is not None:
            bucket["my_value"] = q(to_decimal(bucket["my_value"]) + get_user_project_value(user, project))
    return sorted(rows.values(), key=lambda row: row["label"])


def build_dashboard_executive_summary(projects: list[Project]) -> dict[str, object]:
    today = date.today()
    late_projects = [
        project for project in projects
        if project.deadline and project.deadline < today and project.operational_status not in {"CONCLUÍDO", "ENTREGUE", "APROVADO"}
    ]
    upcoming_projects = [
        project for project in projects
        if project.deadline and today <= project.deadline <= today + timedelta(days=7) and project.operational_status not in {"CONCLUÍDO", "ENTREGUE", "APROVADO"}
    ]
    by_discipline = get_financial_rows(projects)["disciplines"][:5]
    by_school = get_financial_rows(projects)["schools"][:5]
    return {
        "late_projects": len(late_projects),
        "upcoming_projects": len(upcoming_projects),
        "by_discipline": by_discipline,
        "by_school": by_school,
    }


def column_exists(table_name: str, column_name: str) -> bool:
    rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(row[1] == column_name for row in rows)


def ensure_db_schema() -> None:
    migrations = {
        "users": [
            "ALTER TABLE users ADD COLUMN designer_id INTEGER",
            "ALTER TABLE users ADD COLUMN can_view_all_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_view_financial_dashboard BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_manage_master_data BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_manage_users BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_create_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_edit_own_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_edit_assigned_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_edit_all_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_delete_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN can_export_projects BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN permitted_school_ids TEXT",
            "ALTER TABLE users ADD COLUMN permitted_discipline_ids TEXT",
            "ALTER TABLE users ADD COLUMN only_participating_projects BOOLEAN NOT NULL DEFAULT 0",
        ],
        "projects": [
            "ALTER TABLE projects ADD COLUMN assigned_user_id INTEGER",
            "ALTER TABLE projects ADD COLUMN created_by_user_id INTEGER",
            "ALTER TABLE projects ADD COLUMN invoice_date DATE",
            "ALTER TABLE projects ADD COLUMN master_id INTEGER",
            "ALTER TABLE projects ADD COLUMN project_category VARCHAR(20) NOT NULL DEFAULT 'ALL/PAS'",
            "ALTER TABLE projects ADD COLUMN manual_total_value NUMERIC(12,2) NOT NULL DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN company_total_value NUMERIC(12,2) NOT NULL DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN invoice_file_original_filename VARCHAR(255)",
            "ALTER TABLE projects ADD COLUMN invoice_file_stored_filename VARCHAR(255)",
            "ALTER TABLE projects ADD COLUMN invoice_file_ext VARCHAR(20)",
            "ALTER TABLE projects ADD COLUMN invoice_file_mime_type VARCHAR(120)",
            "ALTER TABLE projects ADD COLUMN invoice_file_size INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN invoice_file_uploaded_at DATETIME",
            "ALTER TABLE projects ADD COLUMN invoice_file_uploaded_by_user_id INTEGER",
        ],
        "schools": [
            "ALTER TABLE schools ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1",
        ],
        "project_checklist_items": [
            "ALTER TABLE project_checklist_items ADD COLUMN due_date DATE",
        ],
    }
    for table, statements in migrations.items():
        for statement in statements:
            col = statement.split("ADD COLUMN", 1)[1].strip().split()[0]
            if not column_exists(table, col):
                db.session.execute(text(statement))
    db.session.commit()


def sync_project_master_from_items(master: ProjectMaster) -> None:
    items = sorted(master.items, key=lambda item: item.id)
    if not items:
        return
    deadlines = [item.deadline for item in items if item.deadline]
    deliveries = [item.delivery_date for item in items if item.delivery_date]
    statuses = [item.operational_status for item in items if item.operational_status]
    master.macro_status = statuses[-1] if statuses else master.macro_status
    if not master.assigned_user_id:
        master.assigned_user_id = items[0].assigned_user_id


def ensure_project_master_links() -> None:
    changed = False
    projects = Project.query.filter(Project.master_id.is_(None)).all()
    for project in projects:
        master = ProjectMaster(
            code=f"LEG-{project.id:04d}",
            name=project.project_name,
            client_name=project.school.name if project.school else None,
            school_id=project.school_id,
            assigned_user_id=project.assigned_user_id,
            created_by_user_id=project.created_by_user_id,
            macro_status=project.operational_status or "CADASTRADO",
            notes=project.notes,
            active=True,
        )
        db.session.add(master)
        db.session.flush()
        project.master_id = master.id
        changed = True
    if changed:
        db.session.commit()

def user_has_global_scope(user: User) -> bool:
    return user.can_access_all_projects() and user.can_see_global_financial_data()


def scope_allows_project(user: User, project: Project) -> bool:
    school_ids = user.get_permitted_school_ids()
    if school_ids and project.school_id not in school_ids:
        return False
    discipline_ids = user.get_permitted_discipline_ids()
    if discipline_ids and project.discipline_id not in discipline_ids:
        return False
    return True


def scope_allows_master(user: User, master: ProjectMaster) -> bool:
    school_ids = user.get_permitted_school_ids()
    if school_ids and master.school_id not in school_ids:
        return False
    discipline_ids = user.get_permitted_discipline_ids()
    if discipline_ids:
        if not any(item.discipline_id in discipline_ids for item in master.items):
            return False
    return True


def master_visible_to_user(user: User, master: ProjectMaster) -> bool:
    if not scope_allows_master(user, master):
        return False
    if user_has_global_scope(user):
        return True
    if master.created_by_user_id == user.id or master.assigned_user_id == user.id:
        return True
    for item in master.items:
        if user_can_access_project(user, item):
            return True
    return False


def get_visible_project_masters_query(user: User):
    query = ProjectMaster.query
    school_ids = user.get_permitted_school_ids()
    if school_ids:
        query = query.filter(ProjectMaster.school_id.in_(school_ids))
    if user_has_global_scope(user) and not user.only_participating_projects:
        discipline_ids = user.get_permitted_discipline_ids()
        if discipline_ids:
            query = query.filter(ProjectMaster.items.any(Project.discipline_id.in_(discipline_ids)))
        return query
    item_master_ids = [row[0] for row in get_visible_projects_query(user).with_entities(Project.master_id).distinct().all() if row[0] is not None]
    filters = [ProjectMaster.created_by_user_id == user.id, ProjectMaster.assigned_user_id == user.id]
    if item_master_ids:
        filters.append(ProjectMaster.id.in_(item_master_ids))
    if filters:
        query = query.filter(db.or_(*filters))
    else:
        query = query.filter(text('1=0'))
    discipline_ids = user.get_permitted_discipline_ids()
    if discipline_ids:
        query = query.filter(ProjectMaster.items.any(Project.discipline_id.in_(discipline_ids)))
    return query


def get_project_master_or_403(master_id: int) -> ProjectMaster:
    master = ProjectMaster.query.get_or_404(master_id)
    if not master_visible_to_user(g.user, master):
        flash("Você não tem acesso a este projeto principal.", "danger")
        abort(403)
    return master


def summarize_master_financial(master: ProjectMaster) -> dict[str, Decimal | int | date | None]:
    items = list(master.items)
    deadlines = [item.deadline for item in items if item.deadline]
    deliveries = [item.delivery_date for item in items if item.delivery_date]
    return {
        "items": len(items),
        "allpas_items": sum(1 for item in items if (item.project_category or "ALL/PAS").upper() == "ALL/PAS"),
        "private_items": sum(1 for item in items if (item.project_category or "ALL/PAS").upper() == "PRIVADO"),
        "invoice_files_count": sum(1 for item in items if item.invoice_file_original_filename),
        "has_invoice_file": any(bool(item.invoice_file_original_filename) for item in items),
        "area": q(sum((to_decimal(item.area_m2) for item in items), Decimal("0.00"))),
        "invoice": q(sum((to_decimal(item.total_invoice_value) for item in items), Decimal("0.00"))),
        "giulia": q(sum((to_decimal(item.giulia_total_value) for item in items), Decimal("0.00"))),
        "designer": q(sum((to_decimal(item.designer_total_value) for item in items), Decimal("0.00"))),
        "profit": q(sum((to_decimal(item.max_profit_total) for item in items), Decimal("0.00"))),
        "company": q(sum((to_decimal(item.company_total_value) for item in items), Decimal("0.00"))),
        "nearest_deadline": min(deadlines) if deadlines else None,
        "last_delivery": max(deliveries) if deliveries else None,
    }


def upsert_project_master_form_data(master: Optional[ProjectMaster] = None) -> tuple[Optional[ProjectMaster], Optional[str]]:
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip() or None
    client_name = request.form.get("client_name", "").strip() or None
    school_id = int(request.form.get("school_id", "0") or 0)
    assigned_user_id_raw = request.form.get("assigned_user_id", "").strip()
    macro_status = request.form.get("macro_status", "CADASTRADO")
    notes = request.form.get("notes", "").strip() or None
    if not name or not school_id:
        return None, "Preencha nome do projeto principal e escola."
    if g.user.has_scope_school_restriction() and school_id not in g.user.get_permitted_school_ids():
        return None, "Esta escola está fora do escopo permitido para o seu usuário."
    assigned_user_id = int(assigned_user_id_raw) if assigned_user_id_raw else None
    if macro_status not in OPERATIONAL_STATUSES:
        macro_status = "CADASTRADO"
    if master is None:
        master = ProjectMaster(created_by_user_id=g.user.id)
    master.name = name
    master.code = code
    master.client_name = client_name
    master.school_id = school_id
    master.assigned_user_id = assigned_user_id
    master.macro_status = macro_status
    master.notes = notes
    return master, None


def upsert_project_master_from_form(master: Optional[ProjectMaster] = None) -> tuple[Optional[ProjectMaster], Optional[str]]:
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip() or None
    client_name = request.form.get("client_name", "").strip() or None
    school_id = int(request.form.get("school_id", "0") or 0)
    assigned_user_id_raw = request.form.get("assigned_user_id", "").strip()
    macro_status = request.form.get("macro_status", "CADASTRADO")
    notes = request.form.get("notes", "").strip() or None
    if not name or not school_id:
        return None, "Preencha nome do projeto principal e escola."
    if g.user.has_scope_school_restriction() and school_id not in g.user.get_permitted_school_ids():
        return None, "Esta escola está fora do escopo permitido para o seu usuário.", None
    assigned_user_id = int(assigned_user_id_raw) if assigned_user_id_raw else None
    if macro_status not in OPERATIONAL_STATUSES:
        macro_status = "CADASTRADO"
    if master is None:
        master = ProjectMaster(created_by_user_id=g.user.id)
    master.name = name
    master.code = code
    master.client_name = client_name
    master.school_id = school_id
    master.assigned_user_id = assigned_user_id
    master.macro_status = macro_status
    master.notes = notes
    return master, None


def get_linked_projects_query(user: User):
    filters = [Project.created_by_user_id == user.id, Project.assigned_user_id == user.id]
    if user.designer_id:
        filters.append(Project.designer_id == user.designer_id)
    query = Project.query.filter(db.or_(*filters))
    school_ids = user.get_permitted_school_ids()
    if school_ids:
        query = query.filter(Project.school_id.in_(school_ids))
    discipline_ids = user.get_permitted_discipline_ids()
    if discipline_ids:
        query = query.filter(Project.discipline_id.in_(discipline_ids))
    return query


def get_visible_projects_query(user: User):
    school_ids = user.get_permitted_school_ids()
    discipline_ids = user.get_permitted_discipline_ids()
    if user_has_global_scope(user) and not user.only_participating_projects:
        query = Project.query
        if school_ids:
            query = query.filter(Project.school_id.in_(school_ids))
        if discipline_ids:
            query = query.filter(Project.discipline_id.in_(discipline_ids))
        return query
    return get_linked_projects_query(user)


def user_can_access_project(user: User, project: Project) -> bool:
    if not scope_allows_project(user, project):
        return False
    if user_has_global_scope(user) and not user.only_participating_projects:
        return True
    if project.created_by_user_id == user.id or project.assigned_user_id == user.id:
        return True
    if user.designer_id and project.designer_id == user.designer_id:
        return True
    return False


def user_can_edit_project(user: User, project: Project) -> bool:
    if user.can_edit_any_project():
        return True
    if user.role == "PROJETISTA":
        return False
    if user.can_edit_own_created_projects() and project.created_by_user_id == user.id:
        return True
    if user.can_edit_projects_assigned_to_me() and project.assigned_user_id == user.id:
        return True
    return False


def get_user_project_value(user: User, project: Project) -> Decimal:
    value = Decimal("0.00")

    is_same_linked_designer = (
        user.designer_id is not None
        and project.designer_id is not None
        and user.designer_id == project.designer_id
    )

    is_assigned_project_designer = (
        user.role == "PROJETISTA"
        and project.executor_type == "DESIGNER"
        and project.assigned_user_id == user.id
    )

    if is_same_linked_designer or is_assigned_project_designer:
        value += to_decimal(project.designer_total_value)

    return q(value)


def sum_user_project_value(user: User, projects: list[Project]) -> Decimal:
    total = Decimal("0.00")
    for project in projects:
        total += get_user_project_value(user, project)
    return q(total)


def get_user_dependency_summary(user: User) -> dict[str, int]:
    return {
        "created_projects": Project.query.filter_by(created_by_user_id=user.id).count(),
        "assigned_projects": Project.query.filter_by(assigned_user_id=user.id).count(),
        "history_entries": ProjectHistory.query.filter_by(user_id=user.id).count(),
    }


def user_has_dependencies(user: User) -> bool:
    deps = get_user_dependency_summary(user)
    return any(deps.values())


def get_designer_dependency_summary(designer: Designer) -> dict[str, int]:
    return {
        "projects": Project.query.filter_by(designer_id=designer.id).count(),
        "linked_users": User.query.filter_by(designer_id=designer.id).count(),
    }


def get_school_dependency_summary(school: School) -> dict[str, int]:
    return {
        "projects": Project.query.filter_by(school_id=school.id).count(),
    }


def get_discipline_dependency_summary(discipline: Discipline) -> dict[str, int]:
    return {
        "projects": Project.query.filter_by(discipline_id=discipline.id).count(),
        "pricing_rules": PricingRule.query.filter_by(discipline_id=discipline.id).count(),
    }


def transfer_user_dependencies(source_user: User, target_user: User) -> dict[str, int]:
    moved_created = Project.query.filter_by(created_by_user_id=source_user.id).update(
        {Project.created_by_user_id: target_user.id}, synchronize_session=False
    )
    moved_assigned = Project.query.filter_by(assigned_user_id=source_user.id).update(
        {Project.assigned_user_id: target_user.id}, synchronize_session=False
    )
    moved_history = ProjectHistory.query.filter_by(user_id=source_user.id).update(
        {ProjectHistory.user_id: target_user.id}, synchronize_session=False
    )
    return {
        "created_projects": moved_created or 0,
        "assigned_projects": moved_assigned or 0,
        "history_entries": moved_history or 0,
    }


def cleanup_designer_users() -> None:
    test_email = "projetista.teste@empresa.local"
    designer_users = User.query.filter(User.role == "PROJETISTA").order_by(User.created_at.asc()).all()
    test_user = next((user for user in designer_users if user.email.lower() == test_email), None)

    if test_user is None:
        default_designer = Designer.query.filter_by(active=True).order_by(Designer.name.asc()).first()
        test_user = User(
            name="Projetista Teste",
            email=test_email,
            role="PROJETISTA",
            active=True,
            designer_id=default_designer.id if default_designer else None,
            can_view_all_projects=False,
            can_view_financial_dashboard=False,
            can_manage_master_data=False,
            can_manage_users=False,
            can_create_projects=False,
            can_edit_own_projects=False,
            can_edit_assigned_projects=False,
            can_edit_all_projects=False,
            can_delete_projects=False,
            can_export_projects=False,
        )
        test_user.set_password("teste123")
        db.session.add(test_user)
        db.session.flush()

    for user in designer_users:
        if user.id == test_user.id:
            continue

        if user_has_dependencies(user):
            transfer_user_dependencies(user, test_user)

        user.active = False
        user.can_view_all_projects = False
        user.can_view_financial_dashboard = False
        user.can_manage_master_data = False
        user.can_manage_users = False
        user.can_create_projects = False
        user.can_edit_own_projects = False
        user.can_edit_assigned_projects = False
        user.can_edit_all_projects = False
        user.can_delete_projects = False
        user.can_export_projects = False
        db.session.flush()
        db.session.delete(user)

    test_user.active = True
    test_user.role = "PROJETISTA"
    test_user.can_view_all_projects = False
    test_user.can_view_financial_dashboard = False
    test_user.can_manage_master_data = False
    test_user.can_manage_users = False
    test_user.can_create_projects = False
    test_user.can_edit_own_projects = False
    test_user.can_edit_assigned_projects = False
    test_user.can_edit_all_projects = False
    test_user.can_delete_projects = False
    test_user.can_export_projects = False


def ensure_projectist_visibility_data() -> None:
    test_user = User.query.filter_by(email="projetista.teste@empresa.local").first()
    if not test_user:
        return

    designer = Designer.query.filter_by(name="Projetista Teste").first()
    if designer is None:
        designer = Designer(name="Projetista Teste", email="projetista.teste@empresa.local", active=True)
        db.session.add(designer)
        db.session.flush()

    test_user.designer_id = designer.id
    test_user.role = "PROJETISTA"
    test_user.active = True
    test_user.can_view_all_projects = False
    test_user.can_view_financial_dashboard = False
    test_user.can_manage_master_data = False
    test_user.can_manage_users = False
    test_user.can_create_projects = False
    test_user.can_edit_own_projects = False
    test_user.can_edit_assigned_projects = False
    test_user.can_edit_all_projects = False
    test_user.can_delete_projects = False
    test_user.can_export_projects = False

    # Corrige projetos sem vínculo com Projeto Mestre quando há um único mestre compatível na escola.
    orphan_projects = Project.query.filter(Project.master_id.is_(None)).all()
    for project in orphan_projects:
        matches = ProjectMaster.query.filter_by(school_id=project.school_id, active=True).all()
        if len(matches) == 1:
            project.master_id = matches[0].id

    # Garante pelo menos um projeto de teste atrelado ao projetista, com datas na competência atual.
    linked_project = (
        Project.query.filter(
            Project.executor_type == "DESIGNER",
            Project.designer_id == designer.id,
            Project.assigned_user_id == test_user.id,
        )
        .order_by(Project.id.asc())
        .first()
    )

    if linked_project is None:
        school = School.query.filter_by(active=True).order_by(School.id.asc()).first()
        discipline = Discipline.query.filter_by(active=True).order_by(Discipline.id.asc()).first()
        if school and discipline:
            master = ProjectMaster.query.filter_by(school_id=school.id, active=True).order_by(ProjectMaster.id.asc()).first()
            if master is None:
                master = ProjectMaster(
                    code="ARQ-001",
                    name="Projeto Mestre Teste",
                    client_name="Cliente Teste",
                    school_id=school.id,
                    assigned_user_id=test_user.id,
                    created_by_user_id=1 if User.query.get(1) else test_user.id,
                    macro_status="EM ANDAMENTO",
                    notes="Projeto mestre de teste para visibilidade do projetista.",
                    active=True,
                )
                db.session.add(master)
                db.session.flush()

            rule = PricingRule.query.filter_by(discipline_id=discipline.id, active=True).first()
            area = Decimal("100.00")
            values = calculate_project_values(area, rule, "DESIGNER") if rule else {
                "invoice_per_m2": Decimal("0.00"),
                "max_profit_per_m2": Decimal("0.00"),
                "giulia_per_m2": Decimal("0.00"),
                "designer_per_m2": Decimal("0.00"),
                "total_invoice": Decimal("0.00"),
                "giulia_total": Decimal("0.00"),
                "designer_total": Decimal("0.00"),
                "max_profit_total": Decimal("0.00"),
            }

            linked_project = Project(
                project_name=f"{discipline.name} - Projetista Teste",
                master_id=master.id,
                school_id=school.id,
                discipline_id=discipline.id,
                area_m2=area,
                executor_type="DESIGNER",
                designer_id=designer.id,
                assigned_user_id=test_user.id,
                created_by_user_id=1 if User.query.get(1) else test_user.id,
                deadline=date(2026, 4, 28),
                delivery_date=date(2026, 4, 25),
                invoice_date=date(2026, 4, 26),
                payment_due_date=date(2026, 4, 30),
                payment_status="EM ABERTO",
                operational_status="EM ANDAMENTO",
                notes="Projeto de teste vinculado ao projetista.",
                invoice_per_m2_snapshot=values["invoice_per_m2"],
                max_profit_per_m2_snapshot=values["max_profit_per_m2"],
                giulia_per_m2_snapshot=values["giulia_per_m2"],
                designer_per_m2_snapshot=values["designer_per_m2"],
                total_invoice_value=values["total_invoice"],
                giulia_total_value=values["giulia_total"],
                designer_total_value=values["designer_total"],
                max_profit_total=values["max_profit_total"],
                company_total_value=Decimal("0.00"),
            )
            db.session.add(linked_project)
            db.session.flush()
    else:
        if linked_project.master_id is None:
            matches = ProjectMaster.query.filter_by(school_id=linked_project.school_id, active=True).all()
            if len(matches) == 1:
                linked_project.master_id = matches[0].id
        linked_project.executor_type = "DESIGNER"
        linked_project.designer_id = designer.id
        linked_project.assigned_user_id = test_user.id
        linked_project.delivery_date = linked_project.delivery_date or date(2026, 4, 25)
        linked_project.invoice_date = linked_project.invoice_date or date(2026, 4, 26)
        linked_project.payment_due_date = linked_project.payment_due_date or date(2026, 4, 30)
        linked_project.payment_status = linked_project.payment_status or "EM ABERTO"
        linked_project.project_category = (linked_project.project_category or "ALL/PAS").upper()

    if linked_project:
        linked_project.executor_type = "DESIGNER"
        if linked_project.project_category == "ALL/PAS":
            rule = PricingRule.query.filter_by(discipline_id=linked_project.discipline_id, active=True).first()
            if rule:
                values = calculate_project_values(q(to_decimal(linked_project.area_m2)), rule, "DESIGNER")
                linked_project.invoice_per_m2_snapshot = values["invoice_per_m2"]
                linked_project.max_profit_per_m2_snapshot = values["max_profit_per_m2"]
                linked_project.giulia_per_m2_snapshot = values["giulia_per_m2"]
                linked_project.designer_per_m2_snapshot = values["designer_per_m2"]
                linked_project.total_invoice_value = values["total_invoice"]
                linked_project.giulia_total_value = values["giulia_total"]
                linked_project.designer_total_value = values["designer_total"]
                linked_project.max_profit_total = values["max_profit_total"]
                linked_project.company_total_value = Decimal("0.00")

    db.session.commit()


def can_delete_user(target: User) -> tuple[bool, str]:
    if not g.user.can_manage_system_users():
        return False, "Você não tem permissão para excluir usuários."
    if target.id == g.user.id:
        return False, "Você não pode excluir o próprio usuário logado."
    deps = get_user_dependency_summary(target)
    if any(deps.values()):
        return False, (
            "Não é possível excluir este usuário porque existem vínculos históricos "
            f"(criados: {deps['created_projects']}, responsáveis: {deps['assigned_projects']}, histórico: {deps['history_entries']})."
        )
    return True, ""


def can_delete_designer(target: Designer) -> tuple[bool, str]:
    if not g.user.can_manage_catalogs():
        return False, "Você não tem permissão para excluir projetistas."
    deps = get_designer_dependency_summary(target)
    if any(deps.values()):
        return False, (
            "Não é possível excluir este projetista porque ele possui vínculos "
            f"(projetos: {deps['projects']}, usuários vinculados: {deps['linked_users']})."
        )
    return True, ""


def can_delete_school(target: School) -> tuple[bool, str]:
    if not g.user.can_manage_catalogs():
        return False, "Você não tem permissão para excluir escolas."
    deps = get_school_dependency_summary(target)
    if deps['projects'] > 0:
        return False, f"Não é possível excluir esta escola porque existem {deps['projects']} projeto(s) vinculados."
    return True, ""


def can_delete_discipline(target: Discipline) -> tuple[bool, str]:
    if not g.user.can_manage_catalogs():
        return False, "Você não tem permissão para excluir disciplinas."
    deps = get_discipline_dependency_summary(target)
    if any(deps.values()):
        return False, (
            "Não é possível excluir esta disciplina porque existem vínculos "
            f"(projetos: {deps['projects']}, tabela de preços: {deps['pricing_rules']})."
        )
    return True, ""


def get_financial_rows(projects: list[Project]) -> dict[str, list[dict[str, object]]]:
    by_designer: dict[str, dict[str, object]] = {}
    by_school: dict[str, dict[str, object]] = {}
    by_discipline: dict[str, dict[str, object]] = {}

    for project in projects:
        designer_name = project.designer.name if project.designer else "Sem projetista"
        school_name = project.school.name if project.school else "Sem escola"
        discipline_name = project.discipline.name if project.discipline else "Sem disciplina"

        designer_bucket = by_designer.setdefault(
            designer_name,
            {"label": designer_name, "projects": 0, "invoice": Decimal("0.00"), "designer": Decimal("0.00")},
        )
        designer_bucket["projects"] += 1
        designer_bucket["invoice"] += to_decimal(project.total_invoice_value)
        designer_bucket["designer"] += to_decimal(project.designer_total_value)

        school_bucket = by_school.setdefault(
            school_name,
            {"label": school_name, "projects": 0, "invoice": Decimal("0.00"), "profit": Decimal("0.00"), "company": Decimal("0.00")},
        )
        school_bucket["projects"] += 1
        school_bucket["invoice"] += to_decimal(project.total_invoice_value)
        school_bucket["profit"] += to_decimal(project.max_profit_total)
        school_bucket["company"] += to_decimal(project.company_total_value)

        discipline_bucket = by_discipline.setdefault(
            discipline_name,
            {"label": discipline_name, "projects": 0, "invoice": Decimal("0.00"), "profit": Decimal("0.00"), "company": Decimal("0.00")},
        )
        discipline_bucket["projects"] += 1
        discipline_bucket["invoice"] += to_decimal(project.total_invoice_value)
        discipline_bucket["profit"] += to_decimal(project.max_profit_total)
        discipline_bucket["company"] += to_decimal(project.company_total_value)

    def normalize(values: dict[str, dict[str, object]], primary: str) -> list[dict[str, object]]:
        rows = []
        for row in values.values():
            normalized = {}
            for key, value in row.items():
                normalized[key] = q(value) if isinstance(value, Decimal) else value
            rows.append(normalized)
        return sorted(rows, key=lambda item: item[primary], reverse=True)

    return {
        "designers": normalize(by_designer, "designer"),
        "schools": normalize(by_school, "profit"),
        "disciplines": normalize(by_discipline, "profit"),
    }


def upsert_company_cost_from_form(cost: CompanyCost | None = None) -> tuple[CompanyCost | None, str | None]:
    description = (request.form.get("description") or "").strip()
    category = (request.form.get("category") or "OUTROS").strip().upper()
    cost_type = (request.form.get("cost_type") or "FIXO").strip().upper()
    value = to_decimal(request.form.get("value") or 0)
    competence_month = normalize_competence_month(request.form.get("competence_month"))
    notes = (request.form.get("notes") or "").strip() or None

    if not description:
        return None, "Informe a descrição do custo."
    if category not in COST_CATEGORIES:
        return None, "Categoria de custo inválida."
    if cost_type not in COST_TYPES:
        return None, "Tipo de custo inválido."
    if value <= 0:
        return None, "Informe um valor maior que zero."

    if cost is None:
        cost = CompanyCost(created_by_user_id=g.user.id if getattr(g, "user", None) else None)

    cost.description = description
    cost.category = category
    cost.cost_type = cost_type
    cost.value = q(value)
    cost.competence_month = competence_month
    cost.notes = notes
    if cost.id:
        cost.active = bool_from_form("active")
    return cost, None


def get_project_or_403(project_id: int) -> Project:
    project = Project.query.get_or_404(project_id)
    if not user_can_access_project(g.user, project):
        flash("Você não tem acesso a este projeto.", "danger")
        raise PermissionError
    return project


def get_project_checklist_items(project_id: int) -> list[ProjectChecklistItem]:
    return (
        ProjectChecklistItem.query
        .filter_by(project_id=project_id)
        .order_by(ProjectChecklistItem.is_done.asc(), ProjectChecklistItem.position.asc(), ProjectChecklistItem.created_at.asc())
        .all()
    )


def build_project_checklist_summary(items: list[ProjectChecklistItem]) -> dict[str, int]:
    today = date.today()
    total = len(items)
    done = sum(1 for item in items if item.is_done)
    pending = total - done
    overdue = sum(1 for item in items if not item.is_done and item.due_date and item.due_date < today)
    due_today = sum(1 for item in items if not item.is_done and item.due_date == today)
    upcoming = sum(1 for item in items if not item.is_done and item.due_date and today < item.due_date <= today + timedelta(days=3))
    return {"total": total, "done": done, "pending": pending, "overdue": overdue, "due_today": due_today, "upcoming": upcoming}


def save_uploaded_attachment(file_storage, *, project: Project | None = None, master: ProjectMaster | None = None) -> tuple[ProjectAttachment | None, str | None]:
    if not file_storage or not file_storage.filename:
        return None, "Selecione um arquivo para enviar."
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None, "Nome de arquivo inválido."
    if not allowed_attachment(filename):
        return None, "Tipo de arquivo não permitido."

    ensure_upload_dir()
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    stored_filename = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, stored_filename)
    file_storage.save(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0

    attachment = ProjectAttachment(
        project_id=project.id if project else None,
        master_id=master.id if master else None,
        user_id=g.user.id if getattr(g, 'user', None) else None,
        original_filename=file_storage.filename,
        stored_filename=stored_filename,
        file_ext=ext,
        mime_type=getattr(file_storage, 'mimetype', None),
        file_size=size,
    )
    return attachment, None


def remove_attachment_file(attachment: ProjectAttachment) -> None:
    if attachment and attachment.stored_filename:
        path = os.path.join(UPLOAD_DIR, attachment.stored_filename)
        if os.path.exists(path):
            os.remove(path)


def save_uploaded_invoice_file(file_storage) -> tuple[dict | None, str | None]:
    if not file_storage or not file_storage.filename:
        return None, None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None, "Nome de arquivo inválido para a nota fiscal."
    if not allowed_attachment(filename):
        return None, "Tipo de arquivo não permitido para a nota fiscal."
    ensure_upload_dir()
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    stored_filename = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, stored_filename)
    file_storage.save(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {
        "original_filename": file_storage.filename,
        "stored_filename": stored_filename,
        "file_ext": ext,
        "mime_type": getattr(file_storage, 'mimetype', None),
        "file_size": size,
    }, None


def remove_invoice_file(project: Project) -> None:
    if project and project.invoice_file_stored_filename:
        path = os.path.join(UPLOAD_DIR, project.invoice_file_stored_filename)
        if os.path.exists(path):
            os.remove(path)


def apply_invoice_file_meta(project: Project, file_meta: dict | None) -> None:
    if not file_meta:
        return
    project.invoice_file_original_filename = file_meta.get("original_filename")
    project.invoice_file_stored_filename = file_meta.get("stored_filename")
    project.invoice_file_ext = file_meta.get("file_ext")
    project.invoice_file_mime_type = file_meta.get("mime_type")
    project.invoice_file_size = int(file_meta.get("file_size") or 0)
    project.invoice_file_uploaded_at = datetime.utcnow()
    project.invoice_file_uploaded_by_user_id = g.user.id if getattr(g, 'user', None) else None


def bool_from_form(field: str) -> bool:
    return request.form.get(field) == "1"


def upsert_project_from_form(project: Optional[Project] = None) -> tuple[Optional[Project], Optional[str], dict | None]:
    project_name = request.form.get("project_name", "").strip()
    master_id_raw = request.form.get("master_id", "").strip()
    school_id = int(request.form.get("school_id", "0") or 0)
    discipline_id = int(request.form.get("discipline_id", "0") or 0)
    project_category = (request.form.get("project_category") or "ALL/PAS").strip().upper()
    area_m2 = to_decimal(request.form.get("area_m2") or 0)
    manual_total_value = to_decimal(request.form.get("manual_total_value") or 0)
    executor_type = request.form.get("executor_type", "").strip() or (project.executor_type if project else "GIULIA")
    designer_id_raw = request.form.get("designer_id", "").strip()
    assigned_user_id_raw = request.form.get("assigned_user_id", "").strip()

    if executor_type not in EXECUTOR_TYPES:
        return None, "Executor inválido.", None
    if project_category not in PROJECT_CATEGORIES:
        return None, "Categoria do projeto inválida.", None

    master_id: Optional[int] = int(master_id_raw) if master_id_raw else None
    master: Optional[ProjectMaster] = db.session.get(ProjectMaster, master_id) if master_id else None
    if master:
        school_id = master.school_id
        if not project_name:
            project_name = master.name
    if not project_name or not school_id or not discipline_id:
        return None, "Preencha nome do projeto, escola e disciplina.", None
    if g.user.has_scope_school_restriction() and school_id not in g.user.get_permitted_school_ids():
        return None, "Esta escola está fora do escopo permitido para o seu usuário.", None
    if g.user.has_scope_discipline_restriction() and discipline_id not in g.user.get_permitted_discipline_ids():
        return None, "Esta disciplina está fora do escopo permitido para o seu usuário.", None

    designer_id: Optional[int] = int(designer_id_raw) if designer_id_raw else None
    assigned_user_id: Optional[int] = int(assigned_user_id_raw) if assigned_user_id_raw else None

    if project_category == "ALL/PAS":
        if area_m2 <= 0:
            return None, "Informe a área em m² para projetos da categoria ALL/PAS.", None
        if executor_type == "DESIGNER" and not designer_id:
            return None, "Selecione o projetista quando o executor for PROJETISTA.", None
        rule = PricingRule.query.filter_by(discipline_id=discipline_id, active=True).first()
        if not rule:
            return None, "Não existe tabela de preço ativa para esta disciplina.", None
        values = calculate_project_values(area_m2, rule, executor_type)
    else:
        if manual_total_value <= 0:
            return None, "Informe o valor manual do projeto para a categoria PRIVADO.", None
        values = calculate_private_project_values(manual_total_value, area_m2)
        designer_id = None

    if project is None:
        project = Project(created_by_user_id=g.user.id)

    project.project_name = project_name
    project.project_category = project_category
    project.master_id = master.id if master else (project.master_id if project and project.master_id else None)
    project.school_id = school_id
    project.discipline_id = discipline_id
    project.area_m2 = q(area_m2)
    project.executor_type = executor_type
    project.designer_id = designer_id if executor_type == "DESIGNER" and project_category == "ALL/PAS" else None
    project.assigned_user_id = assigned_user_id
    project.deadline = parse_date(request.form.get("deadline", ""))
    project.delivery_date = parse_date(request.form.get("delivery_date", ""))
    project.invoice_date = parse_date(request.form.get("invoice_date", ""))
    project.pending = request.form.get("pending", "").strip() or None
    project.revision = request.form.get("revision", "").strip() or None
    project.approved = request.form.get("approved", "").strip() or None
    project.payment_date = parse_date(request.form.get("payment_date", ""))
    project.payment_due_date = parse_date(request.form.get("payment_due_date", ""))

    payment_status = request.form.get("payment_status", "EM ABERTO")
    operational_status = request.form.get("operational_status", "CADASTRADO")
    if payment_status not in PAYMENT_STATUSES:
        payment_status = "EM ABERTO"
    if operational_status not in OPERATIONAL_STATUSES:
        operational_status = "CADASTRADO"

    project.payment_status = payment_status
    project.operational_status = operational_status
    project.notes = request.form.get("notes", "").strip() or None

    project.invoice_per_m2_snapshot = values["invoice_per_m2"]
    project.max_profit_per_m2_snapshot = values["max_profit_per_m2"]
    project.giulia_per_m2_snapshot = values["giulia_per_m2"]
    project.designer_per_m2_snapshot = values["designer_per_m2"]
    project.total_invoice_value = values["total_invoice"]
    project.giulia_total_value = values["giulia_total"]
    project.designer_total_value = values["designer_total"]
    project.max_profit_total = values["max_profit_total"]
    project.manual_total_value = values.get("manual_total", Decimal("0.00"))
    project.company_total_value = values.get("company_total", values["max_profit_total"])

    invoice_file_meta = None
    if g.user.can_view_project_invoice():
        invoice_file_meta, invoice_error = save_uploaded_invoice_file(request.files.get("invoice_file"))
        if invoice_error:
            return None, invoice_error, None

    if master:
        sync_project_master_from_items(master)
    return project, None, invoice_file_meta


@app.before_request
def load_logged_user() -> None:
    user_id = session.get("user_id")
    g.user = db.session.get(User, user_id) if user_id else None


@app.context_processor
def inject_globals() -> dict:
    return {
        "PAYMENT_STATUSES": PAYMENT_STATUSES,
        "OPERATIONAL_STATUSES": OPERATIONAL_STATUSES,
        "EXECUTOR_TYPES": EXECUTOR_TYPES,
        "PROJECT_CATEGORIES": PROJECT_CATEGORIES,
        "COST_TYPES": COST_TYPES,
        "COST_CATEGORIES": COST_CATEGORIES,
        "PROJECT_TAX_RATE": PROJECT_TAX_RATE,
        "APP_NAME": APP_NAME,
        "COMPETENCE_TYPES": COMPETENCE_TYPES,
        "COMPETENCE_LABELS": COMPETENCE_LABELS,
        "COMMENT_TYPES": COMMENT_TYPES,
        "user_can_edit_project": user_can_edit_project,
        "summarize_master_financial": summarize_master_financial,
        "file_size_human": file_size_human,
        "attachment_delete_allowed": attachment_delete_allowed,
        "comment_delete_allowed": comment_delete_allowed,
        "checklist_delete_allowed": checklist_delete_allowed,
        "get_checklist_sla": get_checklist_sla,
        "get_user_project_value": get_user_project_value,
        "get_project_alerts": get_project_alerts,
        "derive_rule_profit_per_m2": derive_rule_profit_per_m2,
        "can_see_global_financial_data": lambda user: user.can_see_global_financial_data() if user else False,
        "can_view_project_invoice": lambda user: user.can_view_project_invoice() if user else False,
        "can_view_company_costs": lambda user: user.can_view_company_costs() if user else False,
        "can_manage_company_costs": lambda user: user.can_manage_company_costs() if user else False,
    }


@app.template_filter("brl")
def brl(value: object) -> str:
    amount = float(value or 0)
    formatted = f"{amount:,.2f}"
    return "R$ " + formatted.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("date_br")
def date_br(value: object) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


# ----------------------------
# Seed
# ----------------------------
def reconcile_project_financials() -> None:
    projects = Project.query.all()
    changed = False
    for project in projects:
        recalculated = calculate_project_snapshot_values(project)
        field_mapping = {
            "invoice_per_m2_snapshot": recalculated["invoice_per_m2"],
            "max_profit_per_m2_snapshot": recalculated["max_profit_per_m2"],
            "giulia_per_m2_snapshot": recalculated["giulia_per_m2"],
            "designer_per_m2_snapshot": recalculated["designer_per_m2"],
            "total_invoice_value": recalculated["total_invoice"],
            "giulia_total_value": recalculated["giulia_total"],
            "designer_total_value": recalculated["designer_total"],
            "max_profit_total": recalculated["max_profit_total"],
            "manual_total_value": recalculated.get("manual_total", Decimal("0.00")),
            "company_total_value": recalculated.get("company_total", Decimal("0.00")),
        }
        for field_name, expected_value in field_mapping.items():
            current_value = q(to_decimal(getattr(project, field_name)))
            expected_value = q(to_decimal(expected_value))
            if current_value != expected_value:
                setattr(project, field_name, expected_value)
                changed = True

    if changed:
        db.session.commit()


def seed_initial_data() -> None:
    if User.query.count() == 0:
        admin = User(
            name="Administrador",
            email="admin@empresa.local",
            role="ADMIN",
            active=True,
            can_view_all_projects=True,
            can_view_financial_dashboard=True,
            can_manage_master_data=True,
            can_manage_users=True,
            can_create_projects=True,
            can_edit_own_projects=True,
            can_edit_assigned_projects=True,
            can_edit_all_projects=True,
            can_delete_projects=True,
            can_export_projects=True,
        )
        admin.set_password("admin123")
        manager = User(
            name="Gestão",
            email="gestao@empresa.local",
            role="GESTAO",
            active=True,
            can_view_all_projects=True,
            can_view_financial_dashboard=True,
            can_manage_master_data=True,
            can_manage_users=False,
            can_create_projects=True,
            can_edit_own_projects=True,
            can_edit_assigned_projects=True,
            can_edit_all_projects=True,
            can_delete_projects=False,
            can_export_projects=True,
        )
        manager.set_password("gestao123")
        db.session.add_all([admin, manager])
        db.session.commit()

    if Discipline.query.count() == 0:
        names = [
            "Elétrico",
            "SPDA",
            "Incêndio",
            "Hidrossanitário",
            "Drenagem Pluvial",
            "Drenagem Pluvial com Ar Condicionado",
            "Climatização",
            "Concreto",
            "Arquitetura",
            "Lógica+CFTV",
            "Exaustão",
        ]
        for name in names:
            db.session.add(Discipline(name=name))
        db.session.commit()

    if CompanyCost.query.count() == 0:
        month = date.today().strftime("%Y-%m")
        db.session.add_all([
            CompanyCost(description="Aluguel da sede", category="ALUGUEL", cost_type="FIXO", value=Decimal("3500.00"), competence_month=month, active=True),
            CompanyCost(description="Condomínio", category="CONDOMINIO", cost_type="FIXO", value=Decimal("850.00"), competence_month=month, active=True),
            CompanyCost(description="Energia elétrica", category="ENERGIA", cost_type="VARIAVEL", value=Decimal("620.00"), competence_month=month, active=True),
            CompanyCost(description="Internet corporativa", category="INTERNET", cost_type="FIXO", value=Decimal("220.00"), competence_month=month, active=True),
        ])
        db.session.commit()

    if PricingRule.query.count() == 0:
        values = {
            "Elétrico": ("1.10", "0.30", "0.80", "0.55", "0.25"),
            "SPDA": ("0.60", "0.24", "0.36", "0.26", "0.10"),
            "Incêndio": ("1.00", "0.40", "0.60", "0.45", "0.15"),
            "Hidrossanitário": ("1.23", "0.53", "0.70", "0.50", "0.20"),
            "Drenagem Pluvial": ("0.60", "0.24", "0.36", "0.25", "0.11"),
            "Drenagem Pluvial com Ar Condicionado": ("0.75", "0.30", "0.45", "0.32", "0.13"),
            "Climatização": ("1.10", "0.41", "0.69", "0.48", "0.21"),
            "Concreto": ("2.10", "0.84", "1.26", "0.00", "1.26"),
            "Arquitetura": ("1.25", "0.35", "0.90", "0.56", "0.34"),
            "Lógica+CFTV": ("1.00", "0.40", "0.60", "0.39", "0.21"),
            "Exaustão": ("1.10", "0.41", "0.69", "0.58", "0.11"),
        }
        for discipline in Discipline.query.all():
            if discipline.name in values:
                total, max_profit, giulia_solo, designer, giulia_when_designer = values[discipline.name]
                db.session.add(
                    PricingRule(
                        discipline_id=discipline.id,
                        total_invoice_per_m2=to_decimal(total),
                        max_profit_per_m2=to_decimal(max_profit),
                        giulia_solo_per_m2=to_decimal(giulia_solo),
                        designer_per_m2=to_decimal(designer),
                        giulia_when_designer_per_m2=to_decimal(giulia_when_designer),
                        active=True,
                    )
                )
        db.session.commit()

    if School.query.count() == 0:
        db.session.add_all(
            [
                School(name="ESCOLA SILVA JARDIM", city="Vitória", state="ES"),
                School(name="ESCOLA LUIS DE CAMÕES", city="Vitória", state="ES"),
                School(name="REITORIA IFV", city="Vitória", state="ES"),
            ]
        )
        db.session.commit()

    admin = User.query.filter(func.lower(User.email) == "admin@empresa.local").first()
    if admin:
        admin.can_view_all_projects = True
        admin.can_view_financial_dashboard = True
        admin.can_manage_master_data = True
        admin.can_manage_users = True
        admin.can_create_projects = True
        admin.can_edit_own_projects = True
        admin.can_edit_assigned_projects = True
        admin.can_edit_all_projects = True
        admin.can_delete_projects = True
        admin.can_export_projects = True

    manager = User.query.filter(func.lower(User.email) == "gestao@empresa.local").first()
    if manager:
        manager.can_view_all_projects = True
        manager.can_view_financial_dashboard = True
        manager.can_manage_master_data = True
        manager.can_create_projects = True
        manager.can_edit_own_projects = True
        manager.can_edit_assigned_projects = True
        manager.can_edit_all_projects = True
        manager.can_export_projects = True

    cleanup_designer_users()
    db.session.commit()


# ----------------------------
# Auth
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.email) == email).first()
        if not user or not user.active or not user.check_password(password):
            flash("Usuário ou senha inválidos.", "danger")
        else:
            session.clear()
            session["user_id"] = user.id
            flash(f"Bem-vindo, {user.name}.", "success")
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Sessão encerrada com sucesso.", "success")
    return redirect(url_for("login"))


# ----------------------------
# Dashboard
# ----------------------------


@app.errorhandler(403)
def handle_forbidden(_error):
    if request.endpoint in {"project_master_detail", "project_master_edit", "project_master_delete", "project_master_attachment_upload", "project_master_attachment_download", "project_master_attachment_delete", "project_master_comment_add", "project_master_comment_delete"}:
        return redirect(url_for("project_masters"))
    if request.endpoint and request.endpoint.startswith("project_"):
        return redirect(url_for("projects"))
    return redirect(url_for("dashboard"))


@app.route("/")
@login_required
def dashboard():
    month = request.args.get("month")
    competence = normalize_competence(request.args.get("competence"))
    query = get_visible_projects_query(g.user)

    total_projects = query.count()
    open_projects = query.filter(Project.operational_status != "CONCLUÍDO").count()
    pending_payments = query.filter(Project.payment_status.in_(["EM ABERTO", "ATRASADO", "PARCIAL"])).count()

    visible_projects = query.order_by(Project.created_at.desc()).all()
    monthly_projects, normalized_month = filter_projects_by_month(visible_projects, month, competence)
    monthly_totals = aggregate_competence_totals(monthly_projects)
    user_project_value_total = sum_user_project_value(g.user, visible_projects)
    user_projects_with_value = sum(1 for project in visible_projects if get_user_project_value(g.user, project) > 0)

    financial_totals = (0, 0, 0, 0)
    if g.user.can_see_global_financial_data():
        financial_totals = query.with_entities(
            func.coalesce(func.sum(Project.total_invoice_value), 0),
            func.coalesce(func.sum(Project.giulia_total_value), 0),
            func.coalesce(func.sum(Project.designer_total_value), 0),
            func.coalesce(func.sum(Project.max_profit_total), 0),
        ).one()

    by_status = (
        query.with_entities(Project.operational_status, func.count(Project.id))
        .group_by(Project.operational_status)
        .order_by(Project.operational_status.asc())
        .all()
    )

    latest_projects = visible_projects[:10]
    latest_projects_with_values = [(project, get_user_project_value(g.user, project)) for project in latest_projects]
    executive = build_dashboard_executive_summary(monthly_projects if g.user.can_see_global_financial_data() else visible_projects)
    critical_projects = build_critical_projects(monthly_projects if g.user.can_see_global_financial_data() else visible_projects)
    personal_monthly_total = q(sum((get_user_project_value(g.user, p) for p in monthly_projects), Decimal("0.00")))
    personal_paid_total = q(sum((get_user_project_value(g.user, p) for p in monthly_projects if p.payment_status == "PAGO"), Decimal("0.00")))
    personal_pending_total = q(sum((get_user_project_value(g.user, p) for p in monthly_projects if p.payment_status in ["EM ABERTO", "PARCIAL"]), Decimal("0.00")))
    personal_overdue_total = q(sum((get_user_project_value(g.user, p) for p in monthly_projects if p.payment_status == "ATRASADO"), Decimal("0.00")))
    cost_summary = build_cost_summary(monthly_projects, normalized_month) if g.user.can_see_global_financial_data() else None
    return render_template(
        "dashboard.html",
        total_projects=total_projects,
        open_projects=open_projects,
        pending_payments=pending_payments,
        total_invoice=financial_totals[0],
        giulia_total=financial_totals[1],
        designer_total=financial_totals[2],
        max_profit_total=financial_totals[3],
        by_status=by_status,
        latest_projects=latest_projects,
        latest_projects_with_values=latest_projects_with_values,
        user_project_value_total=user_project_value_total,
        user_projects_with_value=user_projects_with_value,
        selected_month=normalized_month,
        selected_competence=competence,
        monthly_totals=monthly_totals,
        executive=executive,
        can_view_global_financial=g.user.can_see_global_financial_data(),
        personal_monthly_total=personal_monthly_total,
        personal_paid_total=personal_paid_total,
        personal_pending_total=personal_pending_total,
        personal_overdue_total=personal_overdue_total,
        category_summary=build_category_summary(monthly_projects if g.user.can_see_global_financial_data() else visible_projects),
        cost_summary=cost_summary,
        critical_projects=critical_projects,
    )


# ----------------------------
# Master data
# ----------------------------
@app.route("/schools", methods=["GET", "POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def schools():
    edit_id = request.args.get("edit", type=int)
    edit_item = db.session.get(School, edit_id) if edit_id else None
    if request.method == "POST":
        school_id = request.form.get("school_id", "").strip()
        target = db.session.get(School, int(school_id)) if school_id else None
        name = request.form.get("name", "").strip()
        city = request.form.get("city", "").strip() or None
        state = request.form.get("state", "").strip().upper() or None
        notes = request.form.get("notes", "").strip() or None
        active = bool_from_form("active") if target else True

        existing = School.query.filter(func.lower(School.name) == name.lower()).first() if name else None
        if not name:
            flash("Informe o nome da escola.", "danger")
        elif existing and (target is None or existing.id != target.id):
            flash("Já existe uma escola com esse nome.", "danger")
        else:
            item = target or School(active=True)
            item.name = name
            item.city = city
            item.state = state
            item.notes = notes
            item.active = active if target else True
            creating = target is None
            if creating:
                db.session.add(item)
            db.session.commit()
            add_audit("school", item.id, "CRIADO" if creating else "EDITADO", f"Escola {item.name} {'criada' if creating else 'atualizada'}.")
            db.session.commit()
            flash("Escola cadastrada com sucesso." if creating else "Escola atualizada com sucesso.", "success")
            return redirect(url_for("schools"))

    items = School.query.order_by(School.active.desc(), School.name.asc()).all()
    return render_template("schools.html", items=items, edit_item=edit_item)


@app.route("/disciplines", methods=["GET", "POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def disciplines():
    edit_id = request.args.get("edit", type=int)
    edit_item = db.session.get(Discipline, edit_id) if edit_id else None
    if request.method == "POST":
        discipline_id = request.form.get("discipline_id", "").strip()
        target = db.session.get(Discipline, int(discipline_id)) if discipline_id else None
        name = request.form.get("name", "").strip()
        active = bool_from_form("active") if target else True
        existing = Discipline.query.filter(func.lower(Discipline.name) == name.lower()).first() if name else None
        if not name:
            flash("Informe o nome da disciplina.", "danger")
        elif existing and (target is None or existing.id != target.id):
            flash("Já existe uma disciplina com esse nome.", "danger")
        else:
            item = target or Discipline(active=True)
            item.name = name
            item.active = active if target else True
            creating = target is None
            if creating:
                db.session.add(item)
            db.session.commit()
            add_audit("discipline", item.id, "CRIADO" if creating else "EDITADO", f"Disciplina {item.name} {'criada' if creating else 'atualizada'}.")
            db.session.commit()
            flash("Disciplina cadastrada com sucesso." if creating else "Disciplina atualizada com sucesso.", "success")
            return redirect(url_for("disciplines"))

    items = Discipline.query.order_by(Discipline.active.desc(), Discipline.name.asc()).all()
    return render_template("disciplines.html", items=items, edit_item=edit_item)


@app.route("/designers", methods=["GET", "POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def designers():
    edit_id = request.args.get("edit", type=int)
    edit_item = db.session.get(Designer, edit_id) if edit_id else None
    if request.method == "POST":
        designer_id = request.form.get("designer_id", "").strip()
        target = db.session.get(Designer, int(designer_id)) if designer_id else None
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        active = bool_from_form("active") if target else True
        existing = Designer.query.filter(func.lower(Designer.name) == name.lower()).first() if name else None
        if not name:
            flash("Informe o nome do projetista.", "danger")
        elif existing and (target is None or existing.id != target.id):
            flash("Já existe um projetista com esse nome.", "danger")
        else:
            item = target or Designer(active=True)
            item.name = name
            item.email = email
            item.phone = phone
            item.active = active if target else True
            creating = target is None
            if creating:
                db.session.add(item)
            db.session.commit()
            add_audit("designer", item.id, "CRIADO" if creating else "EDITADO", f"Projetista {item.name} {'criado' if creating else 'atualizado'}.")
            db.session.commit()
            flash("Projetista cadastrado com sucesso." if creating else "Projetista atualizado com sucesso.", "success")
            return redirect(url_for("designers"))

    items = Designer.query.order_by(Designer.active.desc(), Designer.name.asc()).all()
    return render_template("designers.html", items=items, edit_item=edit_item)


@app.route("/pricing", methods=["GET", "POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar a tabela de valores.")
def pricing():
    edit_id = request.args.get("edit", type=int)
    edit_rule = db.session.get(PricingRule, edit_id) if edit_id else None
    if request.method == "POST":
        rule_id = request.form.get("rule_id", "").strip()
        discipline_id = int(request.form.get("discipline_id", "0") or 0)
        if not discipline_id:
            flash("Selecione uma disciplina.", "danger")
            return redirect(url_for("pricing"))

        target = db.session.get(PricingRule, int(rule_id)) if rule_id else PricingRule(discipline_id=discipline_id)
        creating = not bool(rule_id)
        if creating:
            db.session.add(target)
        total_invoice_per_m2 = to_decimal(request.form.get("total_invoice_per_m2"))
        giulia_solo_per_m2 = to_decimal(request.form.get("giulia_solo_per_m2"))
        designer_per_m2 = to_decimal(request.form.get("designer_per_m2"))
        giulia_when_designer_per_m2 = to_decimal(request.form.get("giulia_when_designer_per_m2"))
        requested_profit_per_m2 = to_decimal(request.form.get("max_profit_per_m2"))
        derived_profit_giulia = derive_rule_profit_per_m2(total_invoice_per_m2, giulia_solo_per_m2, Decimal("0.00"))
        derived_profit_designer = derive_rule_profit_per_m2(total_invoice_per_m2, giulia_when_designer_per_m2, designer_per_m2)

        target.discipline_id = discipline_id
        target.total_invoice_per_m2 = q(total_invoice_per_m2)
        target.max_profit_per_m2 = derived_profit_designer
        target.giulia_solo_per_m2 = q(giulia_solo_per_m2)
        target.designer_per_m2 = q(designer_per_m2)
        target.giulia_when_designer_per_m2 = q(giulia_when_designer_per_m2)
        target.active = bool_from_form("active") if rule_id else True
        db.session.commit()
        add_audit("pricing_rule", target.id, "CRIADO" if creating else "EDITADO", f"Regra da disciplina {target.discipline.name} {'criada' if creating else 'atualizada'}.")
        db.session.commit()
        if requested_profit_per_m2 != derived_profit_designer:
            flash(f"Lucro por m² ajustado automaticamente para {derived_profit_designer} com base na regra residual.", "warning")
        elif derived_profit_giulia != derived_profit_designer:
            flash("A regra de lucro do executor GIULIA difere da regra com PROJETISTA; o sistema usa o residual real em cada caso.", "warning")
        flash("Tabela de valores salva com sucesso.", "success")
        return redirect(url_for("pricing"))

    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    rules = PricingRule.query.join(Discipline).order_by(PricingRule.active.desc(), Discipline.name.asc()).all()
    return render_template("pricing.html", disciplines=disciplines_list, rules=rules, edit_rule=edit_rule)


@app.route("/users", methods=["GET", "POST"])
@permissions_required("can_manage_system_users", "Somente administradores ou usuários autorizados podem gerenciar acessos.")
def users():
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "GESTAO")
        password = request.form.get("password", "")
        designer_id_raw = request.form.get("designer_id", "").strip()
        designer_id = int(designer_id_raw) if designer_id_raw else None
        permitted_school_ids = ids_to_csv(request.form.getlist("permitted_school_ids"))
        permitted_discipline_ids = ids_to_csv(request.form.getlist("permitted_discipline_ids"))

        if not name or not email:
            flash("Preencha nome e e-mail.", "danger")
        elif role not in USER_ROLES:
            flash("Perfil inválido.", "danger")
        else:
            existing = User.query.filter(func.lower(User.email) == email).first()
            target = db.session.get(User, int(user_id)) if user_id else None
            if existing and (target is None or existing.id != target.id):
                flash("Já existe um usuário com esse e-mail.", "danger")
            elif target is None and not password:
                flash("Informe a senha para o novo usuário.", "danger")
            else:
                user = target or User(active=True)
                user.name = name
                user.email = email
                user.role = role
                user.designer_id = designer_id
                user.active = bool_from_form("active") if user_id else True
                user.can_view_all_projects = bool_from_form("can_view_all_projects")
                user.can_view_financial_dashboard = bool_from_form("can_view_financial_dashboard")
                user.can_manage_master_data = bool_from_form("can_manage_master_data")
                user.can_manage_users = bool_from_form("can_manage_users")
                user.can_create_projects = bool_from_form("can_create_projects")
                user.can_edit_own_projects = bool_from_form("can_edit_own_projects")
                user.can_edit_assigned_projects = bool_from_form("can_edit_assigned_projects")
                user.can_edit_all_projects = bool_from_form("can_edit_all_projects")
                user.can_delete_projects = bool_from_form("can_delete_projects")
                user.can_export_projects = bool_from_form("can_export_projects")
                user.permitted_school_ids = permitted_school_ids or None
                user.permitted_discipline_ids = permitted_discipline_ids or None
                user.only_participating_projects = bool_from_form("only_participating_projects")

                if user.role == "ADMIN":
                    user.can_view_all_projects = True
                    user.can_view_financial_dashboard = True
                    user.can_manage_master_data = True
                    user.can_manage_users = True
                    user.can_create_projects = True
                    user.can_edit_own_projects = True
                    user.can_edit_assigned_projects = True
                    user.can_edit_all_projects = True
                    user.can_delete_projects = True
                    user.can_export_projects = True
                    user.only_participating_projects = False
                elif user.role == "PROJETISTA":
                    user.can_manage_master_data = False
                    user.can_manage_users = False
                    user.can_create_projects = False
                    user.can_edit_own_projects = False
                    user.can_edit_assigned_projects = False
                    user.can_edit_all_projects = False
                    user.can_delete_projects = False
                    user.only_participating_projects = True

                if password:
                    user.set_password(password)

                creating = target is None
                if creating:
                    db.session.add(user)
                    message = "Usuário criado com sucesso."
                else:
                    message = "Usuário atualizado com sucesso."

                db.session.commit()
                add_audit("user", user.id, "CRIADO" if creating else "EDITADO", f"Usuário {user.email} {'criado' if creating else 'atualizado'}.")
                db.session.commit()
                flash(message, "success")
                return redirect(url_for("users"))

    items = User.query.order_by(User.name.asc()).all()
    designers_list = Designer.query.filter_by(active=True).order_by(Designer.name.asc()).all()
    schools_list = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    edit_id = request.args.get("edit", type=int)
    edit_user = db.session.get(User, edit_id) if edit_id else None
    edit_user_dependencies = get_user_dependency_summary(edit_user) if edit_user else None
    transfer_candidates = [item for item in items if not edit_user or item.id != edit_user.id]
    return render_template(
        "users.html",
        items=items,
        roles=USER_ROLES,
        designers=designers_list,
        schools=schools_list,
        disciplines=disciplines_list,
        edit_user=edit_user,
        edit_user_dependencies=edit_user_dependencies,
        transfer_candidates=transfer_candidates,
    )


@app.route("/costs", methods=["GET", "POST"])
@login_required
def costs():
    if not g.user.can_view_company_costs():
        flash("Seu usuário não possui permissão para visualizar custos.", "danger")
        return redirect(url_for("dashboard"))

    selected_month = normalize_competence_month(request.args.get("month") or request.form.get("competence_month"))
    selected_category = (request.args.get("category") or "").strip().upper()
    selected_cost_type = (request.args.get("cost_type") or "").strip().upper()

    if request.method == "POST":
        if not g.user.can_manage_company_costs():
            flash("Seu usuário não possui permissão para gerenciar custos.", "danger")
            return redirect(url_for("costs", month=selected_month))

        cost_id = request.form.get("cost_id", "").strip()
        target = db.session.get(CompanyCost, int(cost_id)) if cost_id else None
        cost, error = upsert_company_cost_from_form(target)
        if error:
            flash(error, "danger")
        else:
            creating = target is None
            if creating:
                db.session.add(cost)
            db.session.commit()
            add_audit("company_cost", cost.id, "CRIADO" if creating else "EDITADO", f"Custo '{cost.description}' {'criado' if creating else 'atualizado'} na competência {cost.competence_month}.")
            db.session.commit()
            flash("Custo cadastrado com sucesso." if creating else "Custo atualizado com sucesso.", "success")
            return redirect(url_for("costs", month=cost.competence_month))

    query = get_visible_company_costs_query(g.user)
    query = query.filter_by(competence_month=selected_month)
    if selected_category:
        query = query.filter_by(category=selected_category)
    if selected_cost_type:
        query = query.filter_by(cost_type=selected_cost_type)

    items = query.order_by(CompanyCost.active.desc(), CompanyCost.category.asc(), CompanyCost.description.asc()).all()
    edit_id = request.args.get("edit", type=int)
    edit_cost = db.session.get(CompanyCost, edit_id) if edit_id else None
    summary = build_cost_summary([], selected_month)
    return render_template(
        "costs.html",
        items=items,
        edit_cost=edit_cost,
        selected_month=selected_month,
        selected_category=selected_category,
        selected_cost_type=selected_cost_type,
        summary=summary,
    )


@app.route("/costs/<int:cost_id>/toggle-active", methods=["POST"])
@login_required
def cost_toggle_active(cost_id: int):
    if not g.user.can_manage_company_costs():
        flash("Seu usuário não possui permissão para alterar custos.", "danger")
        return redirect(url_for("costs"))
    cost = CompanyCost.query.get_or_404(cost_id)
    cost.active = not cost.active
    db.session.commit()
    add_audit("company_cost", cost.id, "ATIVADO" if cost.active else "INATIVADO", f"Custo '{cost.description}' {'ativado' if cost.active else 'inativado'}.")
    db.session.commit()
    flash("Status do custo atualizado com sucesso.", "success")
    return redirect(url_for("costs", month=cost.competence_month))


@app.route("/costs/<int:cost_id>/delete", methods=["POST"])
@login_required
def cost_delete(cost_id: int):
    if not g.user.can_manage_company_costs():
        flash("Seu usuário não possui permissão para excluir custos.", "danger")
        return redirect(url_for("costs"))
    cost = CompanyCost.query.get_or_404(cost_id)
    description = cost.description
    month = cost.competence_month
    db.session.delete(cost)
    db.session.commit()
    add_audit("company_cost", cost_id, "EXCLUIDO", f"Custo '{description}' excluído.")
    db.session.commit()
    flash("Custo excluído com sucesso.", "success")
    return redirect(url_for("costs", month=month))


@app.route("/financeiro")
@login_required
def financial_overview():
    if not g.user.can_see_global_financial_data():
        flash("Seu usuário não possui permissão para acessar o fechamento financeiro.", "danger")
        return redirect(url_for("dashboard"))

    month = request.args.get("month")
    competence = normalize_competence(request.args.get("competence"))
    all_visible_projects = get_visible_projects_query(g.user).order_by(Project.created_at.desc()).all()
    projects, normalized_month = filter_projects_by_month(all_visible_projects, month, competence)

    totals = aggregate_competence_totals(projects)
    payment_groups = aggregate_payment_groups(projects)
    financial_rows = get_financial_rows(projects)
    user_summary = build_user_financial_summary(projects)
    giulia_summary = build_giulia_financial_summary(projects)
    executive = build_dashboard_executive_summary(projects)
    cost_summary = build_cost_summary(projects, normalized_month)
    cashflow_summary = build_cashflow_summary(projects, cost_summary)
    dre_summary = build_dre_summary(totals, cost_summary)
    return render_template(
        "financial_overview.html",
        projects=projects[:20],
        selected_month=normalized_month,
        selected_competence=competence,
        totals=totals,
        payment_groups=payment_groups,
        by_designer=financial_rows["designers"],
        by_school=financial_rows["schools"],
        by_discipline=financial_rows["disciplines"],
        by_user=user_summary,
        giulia_summary=giulia_summary,
        executive=executive,
        category_summary=build_category_summary(projects),
        cost_summary=cost_summary,
        cashflow_summary=cashflow_summary,
        dre_summary=dre_summary,
    )




@app.route("/meu-financeiro")
@login_required
def my_financial_overview():
    month = request.args.get("month")
    competence = normalize_competence(request.args.get("competence"))
    all_visible_projects = get_visible_projects_query(g.user).order_by(Project.created_at.desc()).all()
    projects, normalized_month = filter_projects_by_month(all_visible_projects, month, competence)

    selected_user = g.user
    user_rows = build_user_financial_summary(projects, selected_user=selected_user)
    row = user_rows[0] if user_rows else {"label": g.user.name, "projects": 0, "total": Decimal("0.00"), "paid": 0, "pending": 0, "overdue": 0, "items": []}
    return render_template(
        "my_financial_overview.html",
        selected_month=normalized_month,
        selected_competence=competence,
        summary=row,
        projects=row["items"],
        category_summary=build_category_summary([project for project, _ in row["items"]], use_personal_values=True, user=g.user),
    )


@app.route("/auditoria")
@permissions_required("can_manage_system_users", "Somente administradores ou usuários autorizados podem acessar a auditoria.")
def audit_logs_page():
    entity_type = (request.args.get("entity_type") or "").strip()
    query = AuditLog.query.order_by(AuditLog.created_at.desc())
    if entity_type:
        query = query.filter_by(entity_type=entity_type)
    logs = query.limit(300).all()
    entity_types = [row[0] for row in db.session.query(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type.asc()).all()]
    return render_template("audit_logs.html", logs=logs, entity_types=entity_types, selected_entity_type=entity_type)


@app.route("/schools/<int:school_id>/toggle-active", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def school_toggle_active(school_id: int):
    school = db.session.get(School, school_id)
    if not school:
        flash("Escola não encontrada.", "danger")
        return redirect(url_for("schools"))
    school.active = not school.active
    db.session.commit()
    add_audit("school", school.id, "ATIVO" if school.active else "INATIVO", f"Escola {school.name} {'reativada' if school.active else 'inativada'}.")
    db.session.commit()
    flash(f"Escola {'reativada' if school.active else 'inativada'} com sucesso.", "success")
    return redirect(url_for("schools"))


@app.route("/disciplines/<int:discipline_id>/toggle-active", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def discipline_toggle_active(discipline_id: int):
    discipline = db.session.get(Discipline, discipline_id)
    if not discipline:
        flash("Disciplina não encontrada.", "danger")
        return redirect(url_for("disciplines"))
    discipline.active = not discipline.active
    db.session.commit()
    add_audit("discipline", discipline.id, "ATIVO" if discipline.active else "INATIVO", f"Disciplina {discipline.name} {'reativada' if discipline.active else 'inativada'}.")
    db.session.commit()
    flash(f"Disciplina {'reativada' if discipline.active else 'inativada'} com sucesso.", "success")
    return redirect(url_for("disciplines"))


@app.route("/designers/<int:designer_id>/toggle-active", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def designer_toggle_active(designer_id: int):
    designer = db.session.get(Designer, designer_id)
    if not designer:
        flash("Projetista não encontrado.", "danger")
        return redirect(url_for("designers"))
    designer.active = not designer.active
    db.session.commit()
    add_audit("designer", designer.id, "ATIVO" if designer.active else "INATIVO", f"Projetista {designer.name} {'reativado' if designer.active else 'inativado'}.")
    db.session.commit()
    flash(f"Projetista {'reativado' if designer.active else 'inativado'} com sucesso.", "success")
    return redirect(url_for("designers"))


@app.route("/pricing/<int:rule_id>/toggle-active", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar a tabela de valores.")
def pricing_toggle_active(rule_id: int):
    rule = db.session.get(PricingRule, rule_id)
    if not rule:
        flash("Regra não encontrada.", "danger")
        return redirect(url_for("pricing"))
    rule.active = not rule.active
    db.session.commit()
    add_audit("pricing_rule", rule.id, "ATIVO" if rule.active else "INATIVO", f"Regra da disciplina {rule.discipline.name} {'reativada' if rule.active else 'inativada'}.")
    db.session.commit()
    flash(f"Regra {'reativada' if rule.active else 'inativada'} com sucesso.", "success")
    return redirect(url_for("pricing"))


@app.route("/schools/<int:school_id>/delete", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def school_delete(school_id: int):
    school = db.session.get(School, school_id)
    if not school:
        flash("Escola não encontrada.", "danger")
        return redirect(url_for("schools"))
    allowed, message = can_delete_school(school)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("schools"))
    school_name = school.name
    db.session.delete(school)
    db.session.commit()
    add_audit("school", school_id, "EXCLUIDO", f"Escola {school_name} excluída.")
    db.session.commit()
    flash("Escola excluída com sucesso.", "success")
    return redirect(url_for("schools"))


@app.route("/disciplines/<int:discipline_id>/delete", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def discipline_delete(discipline_id: int):
    discipline = db.session.get(Discipline, discipline_id)
    if not discipline:
        flash("Disciplina não encontrada.", "danger")
        return redirect(url_for("disciplines"))
    allowed, message = can_delete_discipline(discipline)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("disciplines"))
    discipline_name = discipline.name
    db.session.delete(discipline)
    db.session.commit()
    add_audit("discipline", discipline_id, "EXCLUIDO", f"Disciplina {discipline_name} excluída.")
    db.session.commit()
    flash("Disciplina excluída com sucesso.", "success")
    return redirect(url_for("disciplines"))


@app.route("/designers/<int:designer_id>/delete", methods=["POST"])
@permissions_required("can_manage_catalogs", "Somente usuários autorizados podem gerenciar cadastros base.")
def designer_delete(designer_id: int):
    designer = db.session.get(Designer, designer_id)
    if not designer:
        flash("Projetista não encontrado.", "danger")
        return redirect(url_for("designers"))
    allowed, message = can_delete_designer(designer)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("designers"))
    designer_name = designer.name
    db.session.delete(designer)
    db.session.commit()
    add_audit("designer", designer_id, "EXCLUIDO", f"Projetista {designer_name} excluído.")
    db.session.commit()
    flash("Projetista excluído com sucesso.", "success")
    return redirect(url_for("designers"))


@app.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@permissions_required("can_manage_system_users", "Somente administradores ou usuários autorizados podem gerenciar acessos.")
def user_toggle_active(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("users"))
    if user.id == g.user.id and user.active:
        flash("Você não pode inativar o próprio usuário logado.", "danger")
        return redirect(url_for("users"))
    user.active = not user.active
    db.session.commit()
    add_audit("user", user.id, "ATIVO" if user.active else "INATIVO", f"Usuário {user.email} {'reativado' if user.active else 'inativado'}.")
    db.session.commit()
    flash(f"Usuário {'reativado' if user.active else 'inativado'} com sucesso.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/transfer", methods=["POST"])
@permissions_required("can_manage_system_users", "Somente administradores ou usuários autorizados podem gerenciar acessos.")
def user_transfer(user_id: int):
    source_user = db.session.get(User, user_id)
    if not source_user:
        flash("Usuário de origem não encontrado.", "danger")
        return redirect(url_for("users"))

    target_user_id = int(request.form.get("target_user_id", "0") or 0)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("Selecione um usuário de destino válido.", "danger")
        return redirect(url_for("users", edit=user_id))
    if target_user.id == source_user.id:
        flash("O usuário de destino deve ser diferente do usuário de origem.", "danger")
        return redirect(url_for("users", edit=user_id))

    moved = transfer_user_dependencies(source_user, target_user)
    db.session.commit()
    add_audit("user", source_user.id, "TRANSFERIDO", f"Vínculos de {source_user.email} transferidos para {target_user.email}.")
    db.session.commit()
    flash(
        "Vínculos transferidos com sucesso "
        f"(criados: {moved['created_projects']}, responsáveis: {moved['assigned_projects']}, histórico: {moved['history_entries']}).",
        "success",
    )
    return redirect(url_for("users", edit=user_id))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@permissions_required("can_manage_system_users", "Somente administradores ou usuários autorizados podem gerenciar acessos.")
def user_delete(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("users"))
    allowed, message = can_delete_user(user)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("users"))
    user_email = user.email
    db.session.delete(user)
    db.session.commit()
    add_audit("user", user_id, "EXCLUIDO", f"Usuário {user_email} excluído.")
    db.session.commit()
    flash("Usuário excluído com sucesso.", "success")
    return redirect(url_for("users"))


# ----------------------------
# Projects
# ----------------------------
# ----------------------------
# Projects
# ----------------------------
@app.route("/project-masters")
@login_required
def project_masters():
    search = request.args.get("search", "").strip()
    items = get_visible_project_masters_query(g.user).order_by(ProjectMaster.created_at.desc()).all()
    items = [item for item in items if item.active]
    if search:
        items = [item for item in items if search.lower() in (item.name or "").lower() or search.lower() in (item.code or "").lower() or search.lower() in (item.client_name or "").lower()]
    summaries = {item.id: summarize_master_financial(item) for item in items}
    master_user_values = {item.id: sum_user_project_value(g.user, list(item.items)) for item in items}
    return render_template("project_masters.html", items=items, summaries=summaries, master_user_values=master_user_values, search=search)


@app.route("/project-masters/new", methods=["GET", "POST"])
@login_required
@permissions_required("can_create_new_projects", "Você não tem permissão para criar projetos principais.")
def project_master_new():
    if request.method == "POST":
        master, error = upsert_project_master_form_data()
        if error:
            flash(error, "danger")
        else:
            db.session.add(master)
            db.session.flush()
            add_audit("project_master", master.id, "create", f"Projeto principal '{master.name}' criado.")
            db.session.commit()
            flash("Projeto principal criado com sucesso.", "success")
            return redirect(url_for("project_master_detail", master_id=master.id))
    schools = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []
    return render_template("project_master_form.html", master=None, schools=schools, users_list=users_list)


@app.route("/project-masters/<int:master_id>")
@login_required
def project_master_detail(master_id: int):
    master = get_project_master_or_403(master_id)
    summary = summarize_master_financial(master)
    items = sorted(master.items, key=lambda item: item.id, reverse=True)
    item_can_edit = {item.id: user_can_edit_project(g.user, item) for item in items}
    attachments = ProjectAttachment.query.filter_by(master_id=master.id).order_by(ProjectAttachment.created_at.desc()).all()
    comments = ProjectComment.query.filter_by(master_id=master.id).order_by(ProjectComment.created_at.desc()).all()
    master_user_value = sum_user_project_value(g.user, items)
    return render_template("project_master_detail.html", master=master, summary=summary, items=items, item_can_edit=item_can_edit, attachments=attachments, comments=comments, master_user_value=master_user_value)


@app.route("/project-masters/<int:master_id>/edit", methods=["GET", "POST"])
@login_required
def project_master_edit(master_id: int):
    master = get_project_master_or_403(master_id)
    if not (g.user.can_edit_any_project() or (g.user.can_edit_projects_assigned_to_me() and master.assigned_user_id == g.user.id) or (g.user.can_edit_own_created_projects() and master.created_by_user_id == g.user.id)):
        flash("Você não tem permissão para editar este projeto principal.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    if request.method == "POST":
        _, error = upsert_project_master_form_data(master)
        if error:
            flash(error, "danger")
        else:
            add_audit("project_master", master.id, "update", f"Projeto principal '{master.name}' atualizado.")
            db.session.commit()
            flash("Projeto principal atualizado com sucesso.", "success")
            return redirect(url_for("project_master_detail", master_id=master.id))
    schools = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []
    return render_template("project_master_form.html", master=master, schools=schools, users_list=users_list)


@app.route("/project-masters/<int:master_id>/delete", methods=["POST"])
@login_required
def project_master_delete(master_id: int):
    master = get_project_master_or_403(master_id)
    if not g.user.can_remove_projects():
        flash("Você não tem permissão para excluir projetos principais.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    master_name = master.name
    for item in list(master.items):
        remove_invoice_file(item)
        for attachment in list(item.attachments):
            remove_attachment_file(attachment)
            db.session.delete(attachment)
        db.session.query(ProjectComment).filter_by(project_id=item.id).delete()
        db.session.query(ProjectHistory).filter_by(project_id=item.id).delete()
        db.session.delete(item)
    for attachment in list(master.attachments):
        remove_attachment_file(attachment)
        db.session.delete(attachment)
    db.session.query(ProjectComment).filter_by(master_id=master.id).delete()
    db.session.delete(master)
    db.session.commit()
    add_audit("project_master", master_id, "EXCLUIDO", f"Projeto principal {master_name} excluído com seus itens vinculados.")
    db.session.commit()
    flash("Projeto principal excluído com sucesso.", "success")
    return redirect(url_for("project_masters"))


@app.route("/project-masters/<int:master_id>/attachments", methods=["POST"])
@login_required
def project_master_attachment_upload(master_id: int):
    master = get_project_master_or_403(master_id)
    if not user_can_comment_master(g.user, master):
        flash("Você não tem permissão para enviar anexos neste projeto principal.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    attachment, error = save_uploaded_attachment(request.files.get("attachment_file"), master=master)
    if error:
        flash(error, "danger")
    else:
        db.session.add(attachment)
        add_audit("project_master_attachment", attachment.id, "CRIADO", f"Anexo '{attachment.original_filename}' enviado para projeto principal {master.name}.")
        db.session.commit()
        flash("Anexo enviado com sucesso.", "success")
    return redirect(url_for("project_master_detail", master_id=master.id))


@app.route("/project-masters/<int:master_id>/attachments/<int:attachment_id>/download")
@login_required
def project_master_attachment_download(master_id: int, attachment_id: int):
    master = get_project_master_or_403(master_id)
    attachment = db.session.get(ProjectAttachment, attachment_id)
    if not attachment or attachment.master_id != master.id:
        abort(404)
    ensure_upload_dir()
    path = os.path.join(UPLOAD_DIR, attachment.stored_filename)
    if not os.path.exists(path):
        flash("Arquivo não encontrado no armazenamento.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    return send_from_directory(UPLOAD_DIR, attachment.stored_filename, as_attachment=True, download_name=attachment.original_filename)


@app.route("/project-masters/<int:master_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def project_master_attachment_delete(master_id: int, attachment_id: int):
    master = get_project_master_or_403(master_id)
    attachment = db.session.get(ProjectAttachment, attachment_id)
    if not attachment or attachment.master_id != master.id:
        flash("Anexo não encontrado.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    if not attachment_delete_allowed(g.user, attachment):
        flash("Você não tem permissão para excluir este anexo.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    original_filename = attachment.original_filename
    remove_attachment_file(attachment)
    db.session.delete(attachment)
    add_audit("project_master_attachment", attachment_id, "EXCLUIDO", f"Anexo '{original_filename}' removido do projeto principal {master.name}.")
    db.session.commit()
    flash("Anexo excluído com sucesso.", "success")
    return redirect(url_for("project_master_detail", master_id=master.id))


@app.route("/project-masters/<int:master_id>/comments", methods=["POST"])
@login_required
def project_master_comment_add(master_id: int):
    master = get_project_master_or_403(master_id)
    if not user_can_comment_master(g.user, master):
        flash("Você não tem permissão para comentar neste projeto principal.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    content = (request.form.get("content") or "").strip()
    comment_type = request.form.get("comment_type", "GERAL")
    if comment_type not in COMMENT_TYPES:
        comment_type = "GERAL"
    if not content:
        flash("Digite um comentário antes de enviar.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    comment = ProjectComment(master_id=master.id, user_id=g.user.id, comment_type=comment_type, content=content)
    db.session.add(comment)
    add_audit("project_master_comment", None, "CRIADO", f"Comentário {comment_type} adicionado ao projeto principal {master.name}.")
    db.session.commit()
    flash("Comentário registrado com sucesso.", "success")
    return redirect(url_for("project_master_detail", master_id=master.id))


@app.route("/project-masters/<int:master_id>/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def project_master_comment_delete(master_id: int, comment_id: int):
    master = get_project_master_or_403(master_id)
    comment = db.session.get(ProjectComment, comment_id)
    if not comment or comment.master_id != master.id:
        flash("Comentário não encontrado.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    if not comment_delete_allowed(g.user, comment):
        flash("Você não tem permissão para excluir este comentário.", "danger")
        return redirect(url_for("project_master_detail", master_id=master.id))
    db.session.delete(comment)
    add_audit("project_master_comment", comment_id, "EXCLUIDO", f"Comentário removido do projeto principal {master.name}.")
    db.session.commit()
    flash("Comentário excluído com sucesso.", "success")
    return redirect(url_for("project_master_detail", master_id=master.id))


@app.route("/pendencias")
@login_required
def issue_center():
    school_id = request.args.get("school_id", type=int)
    discipline_id = request.args.get("discipline_id", type=int)
    assigned_user_id = request.args.get("assigned_user_id", type=int)
    operational_status = request.args.get("operational_status", type=str)
    issue_type = (request.args.get("issue_type") or "").strip().upper()
    month = request.args.get("month", type=str)
    search = (request.args.get("search") or "").strip()

    query = get_visible_projects_query(g.user)
    if school_id:
        query = query.filter_by(school_id=school_id)
    if discipline_id:
        query = query.filter_by(discipline_id=discipline_id)
    if assigned_user_id:
        query = query.filter_by(assigned_user_id=assigned_user_id)
    if operational_status:
        query = query.filter_by(operational_status=operational_status)
    if search:
        like = f"%{search}%"
        query = query.filter(Project.project_name.ilike(like))

    items = query.order_by(Project.deadline.asc().nulls_last(), Project.created_at.desc()).all()
    items, normalized_month = filter_projects_by_month(items, month)
    rows = build_issue_center_rows(items, issue_type=issue_type)
    summary = summarize_issue_center(rows)

    schools_list = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []
    return render_template(
        "issue_center.html",
        rows=rows,
        summary=summary,
        schools=schools_list,
        disciplines=disciplines_list,
        users_list=users_list,
        selected_school_id=school_id,
        selected_discipline_id=discipline_id,
        selected_assigned_user_id=assigned_user_id,
        selected_operational_status=operational_status,
        selected_issue_type=issue_type,
        selected_month=normalized_month,
        search=search,
    )


@app.route("/projects")
@login_required
def projects():
    school_id = request.args.get("school_id", type=int)
    discipline_id = request.args.get("discipline_id", type=int)
    payment_status = request.args.get("payment_status", type=str)
    operational_status = request.args.get("operational_status", type=str)
    executor_type = request.args.get("executor_type", type=str)
    assigned_user_id = request.args.get("assigned_user_id", type=int)
    master_id = request.args.get("master_id", type=int)
    month = request.args.get("month", type=str)
    search = (request.args.get("search") or "").strip()

    query = get_visible_projects_query(g.user)
    if school_id:
        query = query.filter_by(school_id=school_id)
    if discipline_id:
        query = query.filter_by(discipline_id=discipline_id)
    if payment_status:
        query = query.filter_by(payment_status=payment_status)
    if operational_status:
        query = query.filter_by(operational_status=operational_status)
    if executor_type:
        query = query.filter_by(executor_type=executor_type)
    if assigned_user_id:
        query = query.filter_by(assigned_user_id=assigned_user_id)
    if master_id:
        query = query.filter_by(master_id=master_id)
    if search:
        like = f"%{search}%"
        query = query.filter(Project.project_name.ilike(like))

    items = query.order_by(Project.created_at.desc()).all()
    items, normalized_month = filter_projects_by_month(items, month)
    schools_list = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []
    masters_list = get_visible_project_masters_query(g.user).order_by(ProjectMaster.name.asc()).all()

    item_user_values = {item.id: get_user_project_value(g.user, item) for item in items}
    item_can_edit = {item.id: user_can_edit_project(g.user, item) for item in items}
    item_alerts = {item.id: get_project_alerts(item) for item in items}

    return render_template(
        "projects.html",
        items=items,
        item_user_values=item_user_values,
        item_can_edit=item_can_edit,
        item_alerts=item_alerts,
        schools=schools_list,
        disciplines=disciplines_list,
        users_list=users_list,
        masters_list=masters_list,
        selected_school_id=school_id,
        selected_discipline_id=discipline_id,
        selected_payment_status=payment_status,
        selected_operational_status=operational_status,
        selected_executor_type=executor_type,
        selected_assigned_user_id=assigned_user_id,
        selected_master_id=master_id,
        selected_month=normalized_month,
        search=search,
    )


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def project_new():
    if not g.user.can_create_new_projects():
        flash("Seu usuário não tem permissão para criar projetos.", "danger")
        return redirect(url_for("projects"))
    schools_list = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    designers_list = Designer.query.filter_by(active=True).order_by(Designer.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []
    selected_master_id = request.args.get("master_id", type=int) or request.form.get("master_id", type=int)
    selected_master = db.session.get(ProjectMaster, selected_master_id) if selected_master_id else None

    if request.method == "POST":
        project, error, invoice_file_meta = upsert_project_from_form()
        if error:
            flash(error, "danger")
        else:
            if invoice_file_meta:
                remove_invoice_file(project)
                apply_invoice_file_meta(project, invoice_file_meta)
            if project.assigned_user_id is None:
                project.assigned_user_id = g.user.id
            db.session.add(project)
            db.session.flush()
            add_history(project, "CRIADO", "Projeto criado com cálculo automático.")
            sync_project_master_from_items(project.master) if project.master else None
            db.session.commit()
            add_audit("project", project.id, "CRIADO", f"Projeto {project.project_name} criado.")
            db.session.commit()
            flash("Projeto cadastrado com cálculo automático concluído.", "success")
            if project.master_id:
                return redirect(url_for("project_master_detail", master_id=project.master_id))
            return redirect(url_for("projects"))

    return render_template(
        "project_form.html",
        project=None,
        schools=schools_list,
        disciplines=disciplines_list,
        designers=designers_list,
        users_list=users_list,
        selected_master=selected_master,
        masters_list=get_visible_project_masters_query(g.user).filter_by(active=True).order_by(ProjectMaster.name.asc()).all(),
    )


@app.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    history = ProjectHistory.query.filter_by(project_id=project.id).order_by(ProjectHistory.created_at.desc()).all()
    attachments = ProjectAttachment.query.filter_by(project_id=project.id).order_by(ProjectAttachment.created_at.desc()).all()
    comments = ProjectComment.query.filter_by(project_id=project.id).order_by(ProjectComment.created_at.desc()).all()
    checklist_items = get_project_checklist_items(project.id)
    checklist_summary = build_project_checklist_summary(checklist_items)
    checklist_users = User.query.filter_by(active=True).order_by(User.name.asc()).all() if user_can_edit_project(g.user, project) else []
    user_project_value = get_user_project_value(g.user, project)
    master_summary = summarize_master_financial(project.master) if project.master else None
    master_user_value = sum_user_project_value(g.user, [item for item in project.master.items]) if project.master else Decimal("0.00")
    can_edit_project_flag = user_can_edit_project(g.user, project)
    return render_template("project_detail.html", project=project, history=history, attachments=attachments, comments=comments, checklist_items=checklist_items, checklist_summary=checklist_summary, checklist_users=checklist_users, user_project_value=user_project_value, can_edit_project=can_edit_project_flag, can_manage_checklist=can_edit_project_flag, master_summary=master_summary, master_user_value=master_user_value, project_alerts=get_project_alerts(project))


@app.route("/projects/<int:project_id>/checklist", methods=["POST"])
@login_required
def project_checklist_add(project_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    if not user_can_edit_project(g.user, project):
        flash("Você não tem permissão para adicionar itens na checklist deste projeto.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))

    title = (request.form.get("title") or "").strip()
    assigned_user_id = request.form.get("assigned_user_id", type=int)
    due_date = parse_date((request.form.get("due_date") or "").strip())
    if not title:
        flash("Informe a pendência ou tarefa da checklist.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))

    if assigned_user_id and not db.session.get(User, assigned_user_id):
        assigned_user_id = None

    last_position = db.session.query(func.max(ProjectChecklistItem.position)).filter_by(project_id=project.id).scalar()
    next_position = (last_position or 0) + 1
    item = ProjectChecklistItem(
        project_id=project.id,
        title=title,
        assigned_user_id=assigned_user_id,
        created_by_user_id=g.user.id,
        due_date=due_date,
        position=next_position,
    )
    db.session.add(item)
    db.session.flush()
    assigned_label = item.assigned_user.name if item.assigned_user else "sem responsável"
    due_label = f", prazo {fmt_date(item.due_date)}" if item.due_date else ""
    add_history(project, "CHECKLIST", f"Item '{item.title}' adicionado na checklist ({assigned_label}{due_label}).")
    add_audit("project_checklist", item.id, "CRIADO", f"Item '{item.title}' adicionado à checklist do projeto {project.project_name}.")
    db.session.commit()
    flash("Item adicionado à checklist com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/checklist/<int:item_id>/toggle", methods=["POST"])
@login_required
def project_checklist_toggle(project_id: int, item_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    item = db.session.get(ProjectChecklistItem, item_id)
    if not item or item.project_id != project.id:
        flash("Item da checklist não encontrado.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not user_can_comment_project(g.user, project):
        flash("Você não tem permissão para atualizar a checklist deste projeto.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))

    item.is_done = not item.is_done
    item.completed_at = datetime.utcnow() if item.is_done else None
    item.completed_by_user_id = g.user.id if item.is_done else None
    action = "concluído" if item.is_done else "reaberto"
    add_history(project, "CHECKLIST", f"Item '{item.title}' {action}.")
    add_audit("project_checklist", item.id, "CONCLUIDO" if item.is_done else "REABERTO", f"Item '{item.title}' {action} na checklist do projeto {project.project_name}.")
    db.session.commit()
    flash(f"Item da checklist {action} com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/checklist/<int:item_id>/delete", methods=["POST"])
@login_required
def project_checklist_delete(project_id: int, item_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    item = db.session.get(ProjectChecklistItem, item_id)
    if not item or item.project_id != project.id:
        flash("Item da checklist não encontrado.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not checklist_delete_allowed(g.user, item):
        flash("Você não tem permissão para excluir este item da checklist.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))

    title = item.title
    db.session.delete(item)
    add_history(project, "CHECKLIST REMOVIDA", f"Item '{title}' removido da checklist.")
    add_audit("project_checklist", item_id, "EXCLUIDO", f"Item '{title}' removido da checklist do projeto {project.project_name}.")
    db.session.commit()
    flash("Item da checklist excluído com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/attachments", methods=["POST"])
@login_required
def project_attachment_upload(project_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    if not user_can_comment_project(g.user, project):
        flash("Você não tem permissão para enviar anexos neste item.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    attachment, error = save_uploaded_attachment(request.files.get("attachment_file"), project=project)
    if error:
        flash(error, "danger")
    else:
        db.session.add(attachment)
        add_history(project, "ANEXO", f"Anexo '{attachment.original_filename}' enviado.")
        add_audit("project_attachment", attachment.id, "CRIADO", f"Anexo '{attachment.original_filename}' enviado para item {project.project_name}.")
        db.session.commit()
        flash("Anexo enviado com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/attachments/<int:attachment_id>/download")
@login_required
def project_attachment_download(project_id: int, attachment_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    attachment = db.session.get(ProjectAttachment, attachment_id)
    if not attachment or attachment.project_id != project.id:
        abort(404)
    ensure_upload_dir()
    path = os.path.join(UPLOAD_DIR, attachment.stored_filename)
    if not os.path.exists(path):
        flash("Arquivo não encontrado no armazenamento.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    return send_from_directory(UPLOAD_DIR, attachment.stored_filename, as_attachment=True, download_name=attachment.original_filename)


@app.route("/projects/<int:project_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def project_attachment_delete(project_id: int, attachment_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    attachment = db.session.get(ProjectAttachment, attachment_id)
    if not attachment or attachment.project_id != project.id:
        flash("Anexo não encontrado.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not attachment_delete_allowed(g.user, attachment):
        flash("Você não tem permissão para excluir este anexo.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    original_filename = attachment.original_filename
    remove_attachment_file(attachment)
    db.session.delete(attachment)
    add_history(project, "ANEXO REMOVIDO", f"Anexo '{original_filename}' removido.")
    add_audit("project_attachment", attachment_id, "EXCLUIDO", f"Anexo '{original_filename}' removido do item {project.project_name}.")
    db.session.commit()
    flash("Anexo excluído com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/comments", methods=["POST"])
@login_required
def project_comment_add(project_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    if not user_can_comment_project(g.user, project):
        flash("Você não tem permissão para comentar neste item.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    content = (request.form.get("content") or "").strip()
    comment_type = request.form.get("comment_type", "GERAL")
    if comment_type not in COMMENT_TYPES:
        comment_type = "GERAL"
    if not content:
        flash("Digite um comentário antes de enviar.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    comment = ProjectComment(project_id=project.id, user_id=g.user.id, comment_type=comment_type, content=content)
    db.session.add(comment)
    add_history(project, "COMENTÁRIO", f"Comentário {comment_type} adicionado.")
    add_audit("project_comment", None, "CRIADO", f"Comentário {comment_type} adicionado ao item {project.project_name}.")
    db.session.commit()
    flash("Comentário registrado com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def project_comment_delete(project_id: int, comment_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    comment = db.session.get(ProjectComment, comment_id)
    if not comment or comment.project_id != project.id:
        flash("Comentário não encontrado.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not comment_delete_allowed(g.user, comment):
        flash("Você não tem permissão para excluir este comentário.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    db.session.delete(comment)
    add_history(project, "COMENTÁRIO REMOVIDO", f"Comentário removido.")
    add_audit("project_comment", comment_id, "EXCLUIDO", f"Comentário removido do item {project.project_name}.")
    db.session.commit()
    flash("Comentário excluído com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def project_edit(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Projeto não encontrado.", "danger")
        return redirect(url_for("projects"))
    if not user_can_edit_project(g.user, project):
        flash("Você não pode editar este projeto.", "danger")
        return redirect(url_for("projects"))

    schools_list = School.query.filter_by(active=True).order_by(School.name.asc()).all()
    disciplines_list = Discipline.query.filter_by(active=True).order_by(Discipline.name.asc()).all()
    designers_list = Designer.query.filter_by(active=True).order_by(Designer.name.asc()).all()
    users_list = User.query.filter_by(active=True).order_by(User.name.asc()).all() if g.user.can_access_all_projects() else []

    selected_master = project.master
    if request.method == "POST":
        updated_project, error, invoice_file_meta = upsert_project_from_form(project)
        if error:
            flash(error, "danger")
        else:
            if invoice_file_meta:
                remove_invoice_file(updated_project)
                apply_invoice_file_meta(updated_project, invoice_file_meta)
            if updated_project.assigned_user_id is None:
                updated_project.assigned_user_id = g.user.id
            add_history(updated_project, "EDITADO", "Projeto atualizado e recálculo executado.")
            sync_project_master_from_items(updated_project.master) if updated_project.master else None
            db.session.commit()
            add_audit("project", updated_project.id, "EDITADO", f"Projeto {updated_project.project_name} atualizado.")
            db.session.commit()
            flash("Projeto atualizado com sucesso.", "success")
            return redirect(url_for("project_detail", project_id=project.id))

    return render_template(
        "project_form.html",
        project=project,
        schools=schools_list,
        disciplines=disciplines_list,
        designers=designers_list,
        users_list=users_list,
        selected_master=selected_master,
        masters_list=get_visible_project_masters_query(g.user).filter_by(active=True).order_by(ProjectMaster.name.asc()).all(),
    )


@app.route("/projects/<int:project_id>/invoice/download")
@login_required
def project_invoice_download(project_id: int):
    try:
        project = get_project_or_403(project_id)
    except PermissionError:
        return redirect(url_for("projects"))
    if not g.user.can_view_project_invoice():
        flash("Você não tem permissão para visualizar a nota fiscal deste projeto.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not project.invoice_file_stored_filename:
        flash("Este projeto não possui nota fiscal anexada.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    ensure_upload_dir()
    path = os.path.join(UPLOAD_DIR, project.invoice_file_stored_filename)
    if not os.path.exists(path):
        flash("Arquivo da nota fiscal não encontrado no armazenamento.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    return send_from_directory(UPLOAD_DIR, project.invoice_file_stored_filename, as_attachment=True, download_name=project.invoice_file_original_filename)


@app.route("/projects/<int:project_id>/invoice/delete", methods=["POST"])
@login_required
def project_invoice_delete(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Projeto não encontrado.", "danger")
        return redirect(url_for("projects"))
    if not user_can_access_project(g.user, project):
        flash("Você não tem acesso a este projeto.", "danger")
        return redirect(url_for("projects"))
    if not g.user.can_view_project_invoice():
        flash("Você não tem permissão para remover a nota fiscal deste projeto.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not project.invoice_file_stored_filename:
        flash("Este projeto não possui nota fiscal anexada.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    original_name = project.invoice_file_original_filename or "nota fiscal"
    remove_invoice_file(project)
    project.invoice_file_original_filename = None
    project.invoice_file_stored_filename = None
    project.invoice_file_ext = None
    project.invoice_file_mime_type = None
    project.invoice_file_size = 0
    project.invoice_file_uploaded_at = None
    project.invoice_file_uploaded_by_user_id = None
    add_history(project, "NOTA_FISCAL_REMOVIDA", f"Nota fiscal '{original_name}' removida do projeto.")
    db.session.commit()
    add_audit("project_invoice", project.id, "EXCLUIDO", f"Nota fiscal '{original_name}' removida do projeto {project.project_name}.")
    db.session.commit()
    flash("Nota fiscal removida com sucesso.", "success")
    return redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def project_delete(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Projeto não encontrado.", "danger")
        return redirect(url_for("projects"))
    if not g.user.can_remove_projects():
        flash("Você não tem permissão para excluir projetos.", "danger")
        return redirect(url_for("project_detail", project_id=project.id))
    if not user_can_access_project(g.user, project):
        flash("Você não tem acesso a este projeto.", "danger")
        return redirect(url_for("projects"))

    project_name = project.project_name
    remove_invoice_file(project)
    for attachment in list(project.attachments):
        remove_attachment_file(attachment)
        db.session.delete(attachment)
    db.session.query(ProjectChecklistItem).filter_by(project_id=project.id).delete()
    db.session.query(ProjectComment).filter_by(project_id=project.id).delete()
    db.session.query(ProjectHistory).filter_by(project_id=project.id).delete()
    db.session.delete(project)
    db.session.commit()
    add_audit("project", project_id, "EXCLUIDO", f"Projeto {project_name} excluído.")
    db.session.commit()
    flash("Projeto excluído com sucesso.", "success")
    return redirect(url_for("projects"))


@app.route("/projects/export/csv")
@login_required
def projects_export_csv():
    if not g.user.can_export_any_projects():
        flash("Seu usuário não tem permissão para exportar projetos.", "danger")
        return redirect(url_for("projects"))

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    if g.user.can_see_global_financial_data():
        writer.writerow(
            [
                "ID",
                "Projeto",
                "Categoria",
                "Projeto Mestre",
                "Escola",
                "Disciplina",
                "Área m2",
                "Executor",
                "Projetista",
                "Responsável no painel",
                "Criado por",
                "Prazo",
                "Entrega",
                "Status Operacional",
                "Status Pagamento",
                "Valor Nota",
                "Valor Giulia",
                "Valor Projetista",
                "Lucro Maxsuel",
                "Valor Empresa",
            ]
        )
    else:
        writer.writerow(
            [
                "ID",
                "Projeto",
                "Categoria",
                "Projeto Mestre",
                "Escola",
                "Disciplina",
                "Área m2",
                "Executor",
                "Projetista",
                "Responsável no painel",
                "Prazo",
                "Entrega",
                "Status Operacional",
                "Status Pagamento",
                "Meu Valor",
            ]
        )

    for item in get_visible_projects_query(g.user).order_by(Project.created_at.desc()).all():
        if g.user.can_see_global_financial_data():
            writer.writerow(
                [
                    item.id,
                    item.project_name,
                    item.project_category,
                    item.master.name if item.master else "",
                    item.school.name,
                    item.discipline.name,
                    str(item.area_m2),
                    item.executor_type,
                    item.designer.name if item.designer else "",
                    item.assigned_user.name if item.assigned_user else "",
                    item.created_by_user.name if item.created_by_user else "",
                    fmt_date(item.deadline),
                    fmt_date(item.delivery_date),
                    item.operational_status,
                    item.payment_status,
                    str(item.total_invoice_value),
                    str(item.giulia_total_value),
                    str(item.designer_total_value),
                    str(item.max_profit_total),
                    str(item.company_total_value),
                ]
            )
        else:
            writer.writerow(
                [
                    item.id,
                    item.project_name,
                    item.project_category,
                    item.master.name if item.master else "",
                    item.school.name,
                    item.discipline.name,
                    str(item.area_m2),
                    item.executor_type,
                    item.designer.name if item.designer else "",
                    item.assigned_user.name if item.assigned_user else "",
                    fmt_date(item.deadline),
                    fmt_date(item.delivery_date),
                    item.operational_status,
                    item.payment_status,
                    str(get_user_project_value(g.user, item)),
                ]
            )

    filename = f"projetos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.cli.command("init-db")
def init_db_command() -> None:
    db.create_all()
    ensure_db_schema()
    seed_initial_data()
    cleanup_designer_users()
    ensure_project_master_links()
    ensure_projectist_visibility_data()
    reconcile_project_financials()
    print("Banco inicializado com sucesso.")


with app.app_context():
    db.create_all()
    ensure_db_schema()
    seed_initial_data()
    cleanup_designer_users()
    ensure_project_master_links()
    ensure_projectist_visibility_data()
    reconcile_project_financials()


if __name__ == "__main__":
    app.run(debug=True)
