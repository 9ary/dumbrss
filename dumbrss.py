#!/usr/bin/env python3

import os
import flask
import time
import feedparser
import flask.ext.script as script
import flask.ext.sqlalchemy as f_sqlalchemy
import sqlalchemy
import flask.ext.login as flask_login
import flask_wtf
import wtforms
import urllib.parse as urlparse
import urllib.request as urlrequest
import urllib.error as urlerror
from bs4 import BeautifulSoup

app = flask.Flask(__name__)

# Default config
app.config.update(dict(
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(app.root_path, "dumbrss.db")
))
if os.getenv("DRSS_CONFIG") == None:
    os.environ["DRSS_CONFIG"] = os.path.join(app.root_path, "config.py")
app.config.from_envvar("DRSS_CONFIG", silent = True)

if app.config["SECRET_KEY"] == None:
    f = open(os.environ["DRSS_CONFIG"], "a")
    app.config["SECRET_KEY"] = os.urandom(32)
    f.write("SECRET_KEY = " + str(app.config["SECRET_KEY"]) + "\n")
    f.close()

db = f_sqlalchemy.SQLAlchemy(app)
manager = script.Manager(app)

login_manager = flask_login.LoginManager(app)
login_manager.login_view = "login"

# Set the timezone to UTC for consistent time stamps
os.environ["TZ"] = "UTC"
time.tzset()

class Entry(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    feed_id = db.Column(db.Integer, db.ForeignKey("feed.id"))
    feed = db.relationship("Feed", backref = db.backref("entries", lazy = "dynamic"))
    link = db.Column(db.Text)
    title = db.Column(db.Text)
    summary = db.Column(db.Text)
    author = db.Column(db.Text)
    date = db.Column(db.Integer)
    read = db.Column(db.Integer)
    starred = db.Column(db.Integer)

    def __init__(self, feed, link, title, summary, author, date):
        self.feed = feed
        self.link = link
        self.title = title
        self.summary = summary
        self.author = author
        self.date = date
        self.read = 0
        self.starred = 0

    def __repr__(self):
        return "<Entry {0} ({1})>".format(self.id, self.title)

class Feed(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    owner = db.relationship("User", backref = db.backref("feeds", lazy = "dynamic"))
    folder_id = db.Column(db.Integer, db.ForeignKey("folder.id"))
    folder = db.relationship("Folder", backref = db.backref("feeds", lazy = "dynamic"))
    name = db.Column(db.Text)
    icon = db.Column(db.Text)
    link = db.Column(db.Text)
    url = db.Column(db.Text)

    def __init__(self, owner, folder, name, icon, link, url):
        self.owner = owner
        self.folder = folder
        self.name = name
        self.icon = icon
        self.link = link
        self.url = url

    def __repr__(self):
        return "<Feed {0} ({1})>".format(self.id, self.name)

    def fetch(self, commit = True):
        app.logger.info("Fetching " + str(self))
        d = feedparser.parse(self.url)

        for entry in d.entries:
            if not(hasattr(entry, "link")):
                continue
            if self.entries.filter_by(link = entry.link).count() == 0:
                if not(hasattr(entry, "author")):
                    entry.author = None
                if not(hasattr(entry, "summary")):
                    entry.summary = None
                if hasattr(entry, "published_parsed"):
                    date = int(time.mktime(entry.published_parsed))
                elif hasattr(entry, "updated_parsed"):
                    date = int(time.mktime(entry.updated_parsed))
                else:
                    date = int(time.time())
                dbentry = Entry(self, entry.link, entry.title, entry.summary, entry.author, date)
                db.session.add(dbentry)

        if commit:
            db.session.commit()

class Folder(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    owner = db.relationship("User", backref = db.backref("folders", lazy = "dynamic"))
    name = db.Column(db.Text)

    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    def __repr__():
        return "<Folder {0} ({1})>".format(self.id, self.name)

class User(db.Model, flask_login.UserMixin):
    id = db.Column(db.Integer, primary_key = True)
    name = db.Column(db.Text)
    password = db.Column(db.Text)
    salt = db.Column(db.Binary)
    admin = db.Column(db.Integer)

    def get_id(self):
        return self.name

    def __init__(self, name, password, admin):
        self.name = name
        self.salt = os.urandom(16)
        self.password = flask_login.make_secure_token(password.encode(), self.salt)
        self.admin = admin

    def __repr__():
        return "<User {0} ({1})>".format(self.id, self.name)

class LoginForm(flask_wtf.Form):
    import wtforms.validators as v
    username = wtforms.StringField("Username",
            validators = [ v.DataRequired("Please enter your username") ])
    password = wtforms.PasswordField("Password",
            validators = [ v.DataRequired("Please enter your password") ])
    remember = wtforms.BooleanField("Remember me")

    def validate_username(self, field):
        if load_user(field.data) == None:
            raise wtforms.validators.StopValidation("Invalid username")

    def validate_password(form, field):
        user = load_user(form.username.data)
        if user != None:
            password = flask_login.make_secure_token(field.data.encode(), user.salt)
            if password != user.password:
                raise wtforms.validators.StopValidation("Invalid password")

class AddFeedForm(flask_wtf.Form):
    import wtforms.validators as v
    url = wtforms.StringField("URL",
            validators = [ v.DataRequired("Please provide a URL"),
                v.URL(message = "Please enter a valid URL") ])

def redirect_is_local(url):
    url = urlparse.urlparse(urlparse.urljoin(flask.request.host_url, url))
    localhost = urlparse.urlparse(flask.request.host_url)
    return url.scheme in ("http", "https") and url.netloc == localhost.netloc

def flash_errors(form):
    for field, errors in form.errors.items():
        for error in errors:
            flask.flash(error, "danger")

def urlopen_mozilla(url):
    return urlrequest.urlopen(urlrequest.Request(url, headers = { "User-Agent": "Mozilla/5.0" } ))

@app.route("/")
@app.route("/feed/<int:feed_id>")
@app.route("/folder/<int:folder_id>")
@app.route("/starred", defaults = { "starred": True })
@flask_login.login_required
def feedview(folder_id = None, feed_id = None, starred = False):
    a = flask.request.args.get("a")
    if a == "setread" or a == "setstarred":
        entry = Entry.query.get_or_404(flask.request.args.get("id") or 0)
        if entry.feed.owner.id != flask_login.current_user.id:
            flask.abort(401)
        try:
            f = int(flask.request.args.get("f"))
        except ValueError:
            flask.abort(401)
        if not(f in [0, 1]):
            flask.abort(400)
        if a == "setread":
            entry.read = f
        elif a == "setstarred":
            entry.starred = f
        db.session.commit()
        return ""

    entries = Entry.query.order_by(Entry.id.desc())

    if feed_id:
        feed = Feed.query.get_or_404(feed_id)
        if feed.owner_id != flask_login.current_user.id:
            flask.abort(401)
        title = feed.name
        entries = entries.filter_by(feed_id = feed_id)

    elif folder_id:
        folder = Folder.query.get_or_404(folder_id)
        if folder.owner_id != flask_login.current_user.id:
            flask.abort(401)
        title = folder.name
        entries = entries.join("feed").filter_by(folder_id = folder_id)

    elif starred:
        title = "Starred"
        entries = entries.filter_by(starred = 1)

    else:
        title = "Home"

    entries = entries.join("feed").filter_by(owner_id = flask_login.current_user.id)

    try:
        page = int(flask.request.args.get("p") or 1)
    except ValueError:
        page = 1
    entries = entries.paginate(page, 30)

    addfeedform = AddFeedForm()

    return flask.render_template("feedview.html", entries = entries, title = title,
            folder_id = folder_id, feed_id = feed_id, starred = starred,
            addfeedform = addfeedform)

@app.route("/login", methods = [ "GET", "POST" ])
def login():
    if flask_login.current_user.is_authenticated():
        return flask.redirect("/")
    form = LoginForm()
    if form.validate_on_submit():
        user = load_user(form.username.data)
        flask_login.login_user(user, remember = form.remember.data)
        flask.flash("Welcome, " + flask_login.current_user.name + ".", "success")
        next_page = flask.request.args.get("next")
        if not(redirect_is_local(next_page)):
            next_page = None
        return flask.redirect(next_page or "/")
    else:
        flash_errors(form)
    return flask.render_template("login.html", form = form)

@app.route("/logout")
@flask_login.login_required
def logout():
    flask_login.logout_user()
    return flask.redirect("/login")

@login_manager.user_loader
def load_user(username):
    return User.query.filter(sqlalchemy.func.lower(User.name) ==
            sqlalchemy.func.lower(username)).first()

def fetch_feeds():
    for feed in Feed.query.yield_per(1000):
        feed.fetch(commit = False)
    db.session.commit()

def fetch_feed(id):
    f = Feed.query.filter_by(id = id).first().fetch()

@app.route("/addfeed", methods = [ "POST" ])
@flask_login.login_required
def add_feed():
    form = AddFeedForm()
    if form.validate_on_submit():
        url = form.url.data
        print(url)
        f = feedparser.parse(url)
        if flask_login.current_user.feeds.filter_by(url = url).count():
            flask.flash("This feed already exists", "danger")
            return flask.redirect("/")
        page = BeautifulSoup(urlopen_mozilla(f.feed.link))
        icon = page.find("link", rel = "shortcut icon")
        if icon != None:
            icon = urlparse.urljoin(f.feed.link, icon["href"])
        else:
            icon = urlparse.urljoin(f.feed.link, "/favicon.ico")
            try:
                urlopen_mozilla(icon)
            except urlerror.HTTPError:
                icon = None
        newfeed = Feed(flask_login.current_user, None, f.feed.title, icon, f.feed.link, url)
        db.session.add(newfeed)
        db.session.commit()
        newfeed.fetch()
        flask.flash("Feed added!", "success")
        return flask.redirect(flask.url_for("feedview", feed_id = newfeed.id, starred = False))
    flash_errors(form)
    return flask.redirect("/")

@manager.option("-f", "--feed", dest = "id", default = None)
def fetch(id):
    "Fetch feed updates"
    if id == None:
        fetch_feeds()
    else:
        try:
            id = int(id)
        except ValueError:
            print("Feed ID must be an integer")
            return

        try:
            fetch_feed(id)
        except AttributeError:
            print("No feed with ID", id)

@manager.command
def adduser():
    "Add a user to the database"
    username = None
    password = None
    admin = None

    while username == None:
        username = script.prompt("Username")

    while password == None:
        password = script.prompt_pass("Password")

    while admin == None:
        admin = script.prompt_bool("Admin")

    if load_user(username) == None:
        user = User(username, password, 1 if admin else 0)
        db.session.add(user)
        db.session.commit()
        print("Successfully added " + username)
    else:
        print("This user already exists!")

@manager.command
def initdb():
    "Initialize the database"
    db.create_all()

if __name__ == "__main__":
    manager.run()

