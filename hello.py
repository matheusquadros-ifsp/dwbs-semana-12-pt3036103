import os
from datetime import datetime
from threading import Thread

from dotenv import load_dotenv
from flask import Flask, render_template, session, redirect, url_for
from flask_bootstrap import Bootstrap
from flask_moment import Moment
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail as SendGridMail

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pt3036103-chave-secreta-dwbs-aula060c'
app.config['SQLALCHEMY_DATABASE_URI'] = \
    'sqlite:///' + os.path.join(basedir, 'data.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

bootstrap = Bootstrap(app)
moment = Moment(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Funções fixas que a aplicação sempre garante existir no banco.
DEFAULT_ROLES = ['Administrator', 'Moderator', 'User']

# --- Configuração do envio de e-mail via SendGrid (API HTTP, não SMTP) ---
SENDGRID_API_KEY = os.environ.get('API_KEY')
# Remetente: precisa estar verificado no SendGrid (Single Sender
# Verification ou domínio autenticado), senão o envio é rejeitado.
SENDER_EMAIL = 'matheus.quadros@aluno.ifsp.edu.br'
# O Administrador do sistema SEMPRE recebe a notificação.
ADMIN_EMAIL = 'matheus.quadros@aluno.ifsp.edu.br'
# E-mail extra, só enviado se o usuário marcar o campo no formulário.
TEAM_EMAIL = 'flaskaulasweb@zohomail.com'
EMAIL_SUBJECT = 'PT3036103 - Matheus Quadros Leal dos Santos'


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    users = db.relationship('User', backref='role', lazy='dynamic')

    def __repr__(self):
        return '<Role %r>' % self.name


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))

    def __repr__(self):
        return '<User %r>' % self.username


class SentEmail(db.Model):
    """Registro de cada e-mail de notificação efetivamente enviado,
    exibido em /emailsEnviados."""
    __tablename__ = 'sent_emails'
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(64))       # "De": usuário que disparou o envio
    recipients = db.Column(db.String(255))  # "Para": destinatários
    subject = db.Column(db.String(255))
    body = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return '<SentEmail %r -> %r>' % (self.sender, self.recipients)


class UserForm(FlaskForm):
    name = StringField('Qual é o seu nome?', validators=[DataRequired()])
    role = SelectField('Role?:', coerce=int, validators=[DataRequired()])
    notify_team = BooleanField(
        'Deseja enviar e-mail para %s?' % TEAM_EMAIL
    )
    submit = SubmitField('Submit')


def ensure_default_roles():
    """Garante que as 3 funções padrão existam no banco (idempotente)."""
    for role_name in DEFAULT_ROLES:
        if Role.query.filter_by(name=role_name).first() is None:
            db.session.add(Role(name=role_name))
    db.session.commit()


def log_sent_email(username, recipients, subject, body):
    """Persiste no banco um e-mail que foi efetivamente enviado com
    sucesso, para aparecer em /emailsEnviados."""
    with app.app_context():
        formatted_recipients = ', '.join("'%s'" % r for r in recipients)
        record = SentEmail(
            sender=username,
            recipients=formatted_recipients,
            subject=subject,
            body=body,
        )
        db.session.add(record)
        db.session.commit()


def send_async_email(username, notify_team=False):
    """Monta e envia o e-mail via SendGrid (API HTTP). Se aceito (status
    202), registra o envio em SentEmail.

    O Administrador (ADMIN_EMAIL) sempre recebe. TEAM_EMAIL só é incluído
    se `notify_team` for True (controlado pelo checkbox do formulário).

    Chamável diretamente (ex: no `flask shell`, para testar e ver o
    resultado na hora) ou em uma thread separada (uso normal).
    """
    recipients = [ADMIN_EMAIL]
    if notify_team:
        recipients.append(TEAM_EMAIL)

    body = 'Novo usuário cadastrado: %s' % username
    message = SendGridMail(
        from_email=SENDER_EMAIL,
        to_emails=recipients,
        subject=EMAIL_SUBJECT,
        plain_text_content=body,
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print('E-mail enviado para', recipients, '- status code:', response.status_code)
        app.logger.info(
            'E-mail de notificação enviado para %s (status %s).',
            recipients, response.status_code
        )
        if response.status_code == 202:
            log_sent_email(username, recipients, EMAIL_SUBJECT, body)
        return response.status_code == 202
    except Exception as exc:
        print('Erro ao enviar e-mail via SendGrid:', exc)
        app.logger.error('Falha ao enviar e-mail de notificação: %s', exc)
        return False


def send_new_user_notification(username, notify_team=False):
    Thread(target=send_async_email, args=(username, notify_team)).start()


@app.shell_context_processor
def make_shell_context():
    return dict(db=db, Role=Role, User=User, SentEmail=SentEmail,
                send_async_email=send_async_email)


@app.route('/', methods=['GET', 'POST'])
def index():
    ensure_default_roles()

    form = UserForm()
    form.role.choices = [
        (role.id, role.name) for role in Role.query.order_by(Role.name).all()
    ]

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.name.data).first()
        if user is None:
            selected_role = Role.query.get(form.role.data)
            user = User(username=form.name.data, role=selected_role)
            db.session.add(user)
            db.session.commit()
            session['known'] = False
            send_new_user_notification(user.username, form.notify_team.data)
        else:
            session['known'] = True
        session['name'] = form.name.data
        return redirect(url_for('index'))

    users = User.query.order_by(User.id).all()
    roles = Role.query.order_by(Role.name).all()

    return render_template(
        'index.html',
        form=form,
        name=session.get('name'),
        known=session.get('known', False),
        users=users,
        roles=roles,
    )


@app.route('/emailsEnviados')
def emails_enviados():
    sent_emails = SentEmail.query.order_by(SentEmail.timestamp.desc()).all()
    return render_template('emails_enviados.html', sent_emails=sent_emails)


if __name__ == '__main__':
    app.run(debug=True)
