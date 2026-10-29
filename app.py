# -*- coding: utf-8 -*-
"""
校园论坛主程序：基于 Flask + SQLite 的轻量级校园论坛。
功能涵盖：注册登录、板块浏览、发帖、评论回复、点赞、搜索、
热门排序、个人中心、管理员置顶与删除等。
运行方式：python app.py ，然后浏览器访问 http://127.0.0.1:5000 。
"""

# ===== 依赖导入 =====
import os;
import re;
import sqlite3;
from functools import wraps;
from datetime import datetime;

from flask import (
    Flask, g, session, request, redirect, url_for,
    render_template, flash, jsonify, abort,
);
from werkzeug.security import generate_password_hash, check_password_hash;

# ===== 常量定义 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__));  # 项目根目录。
DATABASE = os.path.join(BASE_DIR, "forum.db");          # SQLite 数据库文件路径。
PAGE_SIZE = 15;                                         # 帖子列表每页条数。

# 板块列表：论坛固定的讨论分区。
SECTIONS = ["学习交流", "校园生活", "二手交易", "失物招领", "活动通知", "灌水闲聊"];

# 每个板块对应的图标名：模板中映射为线性 SVG 图标。
SECTION_ICONS = {
    "学习交流": "book",
    "校园生活": "building",
    "二手交易": "bag",
    "失物招领": "search",
    "活动通知": "bell",
    "灌水闲聊": "chat",
};

# 可选头像配色：头像显示昵称首字，颜色从这套暖粉调色板中选取。
AVATARS = ["rose", "coral", "peach", "apricot", "amber", "blush", "mauve", "clay"];
DEFAULT_AVATAR = "rose";

# ===== 应用初始化 =====
app = Flask(__name__);
# 会话签名密钥：开发环境使用固定值，避免重启后登录态失效。
app.config["SECRET_KEY"] = "campus-forum-secret-key-2024";
# 让 Jinja 模板可以直接使用板块与图标常量。
app.jinja_env.globals["SECTIONS"] = SECTIONS;
app.jinja_env.globals["SECTION_ICONS"] = SECTION_ICONS;
app.jinja_env.globals["AVATARS"] = AVATARS;


# ===== 数据库连接管理 =====
def get_db():
    """获取当前请求的数据库连接：同一请求内复用，避免重复连接。"""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE);
        g.db.row_factory = sqlite3.Row;  # 查询结果按列名访问。
        g.db.execute("PRAGMA foreign_keys = ON");  # 开启外键约束。
    return g.db;


@app.teardown_appcontext
def close_db(exception):
    """请求结束时自动关闭数据库连接。"""
    db = g.pop("db", None);
    if db is not None:
        db.close();


def init_db():
    """初始化数据表：首次运行时自动创建，重复执行不会报错。"""
    db = sqlite3.connect(DATABASE);
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT NOT NULL,
            avatar TEXT NOT NULL DEFAULT 'rose',
            bio TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            section TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            is_pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            parent_id INTEGER REFERENCES comments(id),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL REFERENCES users(id),
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id)
        );
        CREATE INDEX IF NOT EXISTS idx_posts_section ON posts(section);
        CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
    """);
    # 兼容旧数据：早期版本用 emoji 存头像，这里统一迁移为配色名。
    db.execute(
        "UPDATE users SET avatar = ? WHERE avatar NOT IN ({})".format(
            ",".join("?" * len(AVATARS))
        ),
        [DEFAULT_AVATAR] + AVATARS,
    );
    db.commit();
    db.close();


def now_str():
    """返回当前时间的字符串格式：统一用于写入数据库。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S");


# ===== 用户认证相关 =====
@app.before_request
def load_current_user():
    """每次请求前从会话中加载当前登录用户，存入 g.user。"""
    g.user = None;
    user_id = session.get("user_id");
    if user_id:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone();
        # 用户被删除时清理失效会话。
        if g.user is None:
            session.clear();


def login_required(view):
    """登录校验装饰器：未登录访问时跳转到登录页。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("请先登录后再操作！", "warning");
            return redirect(url_for("login", next=request.path));
        return view(*args, **kwargs);
    return wrapped;


def admin_required(view):
    """管理员校验装饰器：仅管理员可用。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or not g.user["is_admin"]:
            abort(403);
        return view(*args, **kwargs);
    return wrapped;


# ===== 模板辅助函数 =====
@app.template_filter("timeago")
def timeago(value):
    """时间显示过滤器：把时间字符串转成'几分钟前'这类友好形式。"""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S");
    except (ValueError, TypeError):
        return value or "";
    seconds = (datetime.now() - dt).total_seconds();
    if seconds < 60:
        return "刚刚";
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前";
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前";
    if seconds < 86400 * 30:
        return f"{int(seconds // 86400)} 天前";
    return dt.strftime("%Y-%m-%d");


@app.template_filter("excerpt")
def excerpt(value, length=90):
    """摘要过滤器：截取帖子正文前若干字符作为列表页预览。"""
    text = re.sub(r"\s+", " ", (value or "")).strip();
    if len(text) <= length:
        return text;
    return text[:length] + "…";


# ===== 数据查询辅助 =====
def get_post_with_stats(post_id):
    """查询单个帖子及其作者、点赞数、评论数。"""
    return get_db().execute("""
        SELECT p.*, u.nickname, u.avatar, u.username,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
        FROM posts p JOIN users u ON u.id = p.user_id
        WHERE p.id = ?
    """, (post_id,)).fetchone();


def get_hot_posts(limit=5):
    """查询热门帖子：按点赞、评论、浏览量加权排序。"""
    return get_db().execute("""
        SELECT p.id, p.title, p.views,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
        FROM posts p
        ORDER BY (like_count * 5 + comment_count * 3 + p.views) DESC, p.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall();


def get_site_stats():
    """统计全站数据：用户数、帖子数、评论数。"""
    db = get_db();
    return {
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "posts": db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "comments": db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
    };


# ===== 页面路由：首页与帖子列表 =====
@app.route("/")
def index():
    """首页：帖子列表，支持板块筛选、关键词搜索、最新/热门排序与分页。"""
    db = get_db();
    section = request.args.get("section", "");       # 板块筛选。
    keyword = request.args.get("q", "").strip();     # 搜索关键词。
    sort = request.args.get("sort", "new");          # 排序方式：new 最新，hot 热门。
    if sort not in ("new", "hot"):
        sort = "new";                                # 非法排序参数回落到最新，避免工具栏无选中项。
    # 页码容错：非数字参数一律回落到第一页，避免非法输入导致 500。
    page_arg = request.args.get("page", "1");
    page = int(page_arg) if page_arg.isdigit() else 1;
    page = max(page, 1);

    # 动态拼接查询条件。
    where = [];
    params = [];
    if section in SECTIONS:
        where.append("p.section = ?");
        params.append(section);
    if keyword:
        # 转义 LIKE 的通配符，避免搜索 "%"、"_" 时匹配到全部帖子。
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
        where.append("(p.title LIKE ? ESCAPE '\\' OR p.content LIKE ? ESCAPE '\\')");
        params.extend([f"%{escaped}%", f"%{escaped}%"]);
    where_sql = ("WHERE " + " AND ".join(where)) if where else "";

    # 统计总数用于分页。
    total = db.execute(
        f"SELECT COUNT(*) FROM posts p {where_sql}", params
    ).fetchone()[0];
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1);
    page = min(page, total_pages);

    # 排序规则：置顶永远优先；热门按加权分数，最新按发布时间。
    order_sql = "p.is_pinned DESC, p.created_at DESC";
    if sort == "hot":
        order_sql = """p.is_pinned DESC,
                       ((SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) * 5 +
                        (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) * 3 +
                        p.views) DESC, p.created_at DESC""";

    posts = db.execute(f"""
        SELECT p.*, u.nickname, u.avatar, u.username,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
        FROM posts p JOIN users u ON u.id = p.user_id
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall();

    return render_template(
        "index.html", posts=posts, section=section, keyword=keyword, sort=sort,
        page=page, total_pages=total_pages, total=total,
        hot_posts=get_hot_posts(), stats=get_site_stats(),
    );


# ===== 页面路由：注册 / 登录 / 退出 =====
@app.route("/register", methods=["GET", "POST"])
def register():
    """注册：第一个注册的用户自动成为管理员。"""
    if g.user:
        return redirect(url_for("index"));
    if request.method == "POST":
        username = request.form.get("username", "").strip();
        nickname = request.form.get("nickname", "").strip() or username;
        password = request.form.get("password", "");
        password2 = request.form.get("password2", "");

        # 基础校验：用户名格式、昵称长度、密码长度、两次输入一致。
        if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fa5]{2,20}", username):
            flash("用户名需为 2-20 位字母、数字、下划线或中文！", "error");
        elif not (1 <= len(nickname) <= 20):
            flash("昵称长度需在 1-20 字之间！", "error");
        elif len(password) < 6:
            flash("密码长度至少 6 位！", "error");
        elif password != password2:
            flash("两次输入的密码不一致！", "error");
        else:
            db = get_db();
            exists = db.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone();
            if exists:
                flash("该用户名已被注册！", "error");
            else:
                # 首个注册用户授予管理员权限。
                user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0];
                is_admin = 1 if user_count == 0 else 0;
                db.execute(
                    """INSERT INTO users (username, password_hash, nickname, avatar, is_admin, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (username, generate_password_hash(password), nickname,
                     DEFAULT_AVATAR, is_admin, now_str()),
                );
                db.commit();
                if is_admin:
                    flash("注册成功！你是本站第一位用户，已自动成为管理员！", "success");
                else:
                    flash("注册成功，请登录！", "success");
                return redirect(url_for("login"));
    return render_template("register.html");


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录：校验用户名与密码，成功后写入会话。"""
    if g.user:
        return redirect(url_for("index"));
    if request.method == "POST":
        username = request.form.get("username", "").strip();
        password = request.form.get("password", "");
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone();
        if user and check_password_hash(user["password_hash"], password):
            session.clear();
            session["user_id"] = user["id"];
            flash(f"欢迎回来，{user['nickname']}！", "success");
            # 支持登录后跳回来源页面，但只允许站内相对路径，防止开放重定向。
            # 反斜杠在部分浏览器会被当作斜杠，需一并拒绝（如 /\example.com）。
            next_page = request.args.get("next") or "";
            if (
                not next_page.startswith("/")
                or next_page.startswith("//")
                or "\\" in next_page
            ):
                next_page = url_for("index");
            return redirect(next_page);
        flash("用户名或密码错误！", "error");
    return render_template("login.html");


@app.route("/logout")
def logout():
    """退出登录：清空会话。"""
    session.clear();
    flash("你已退出登录！", "success");
    return redirect(url_for("index"));


# ===== 页面路由：发帖与帖子详情 =====
@app.route("/post/new", methods=["GET", "POST"])
@login_required
def new_post():
    """发布新帖子。"""
    if request.method == "POST":
        section = request.form.get("section", "");
        title = request.form.get("title", "").strip();
        content = request.form.get("content", "").strip();

        if section not in SECTIONS:
            flash("请选择正确的板块！", "error");
        elif not (2 <= len(title) <= 60):
            flash("标题长度需在 2-60 字之间！", "error");
        elif len(content) < 5:
            flash("内容至少 5 个字！", "error");
        else:
            db = get_db();
            cur = db.execute(
                """INSERT INTO posts (user_id, section, title, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (g.user["id"], section, title, content, now_str()),
            );
            db.commit();
            flash("发布成功！", "success");
            return redirect(url_for("post_detail", post_id=cur.lastrowid));
    return render_template("new_post.html");


@app.route("/post/<int:post_id>")
def post_detail(post_id):
    """帖子详情页：浏览量 +1，展示正文与全部评论。"""
    db = get_db();
    # 先确认帖子存在，再自增浏览量，避免对不存在的 id 反复写库。
    exists = db.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone();
    if exists is None:
        abort(404);
    db.execute("UPDATE posts SET views = views + 1 WHERE id = ?", (post_id,));
    db.commit();

    post = get_post_with_stats(post_id);
    if post is None:
        abort(404);

    # 查询评论：附带作者信息与被回复评论的作者昵称。
    comments = db.execute("""
        SELECT c.*, u.nickname, u.avatar, u.username,
               pu.nickname AS parent_nickname
        FROM comments c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN comments pc ON pc.id = c.parent_id
        LEFT JOIN users pu ON pu.id = pc.user_id
        WHERE c.post_id = ?
        ORDER BY c.created_at ASC
    """, (post_id,)).fetchall();

    # 当前用户是否已点赞。
    liked = False;
    if g.user:
        liked = db.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
            (g.user["id"], post_id),
        ).fetchone() is not None;

    return render_template(
        "post_detail.html", post=post, comments=comments, liked=liked,
    );


# ===== 操作路由：评论 / 点赞 / 删除 / 置顶 =====
@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    """发表评论：支持回复某条评论（@ 功能）。"""
    db = get_db();
    post = db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone();
    if post is None:
        abort(404);

    content = request.form.get("content", "").strip();
    parent_id = request.form.get("parent_id") or None;
    # 校验被回复的评论：必须存在且属于当前帖子，否则按普通评论处理。
    if parent_id:
        parent_id = int(parent_id) if parent_id.isdigit() else None;
        if parent_id:
            parent = db.execute(
                "SELECT id FROM comments WHERE id = ? AND post_id = ?",
                (parent_id, post_id),
            ).fetchone();
            if parent is None:
                parent_id = None;
    if len(content) < 2:
        flash("评论至少 2 个字！", "error");
    else:
        db.execute(
            """INSERT INTO comments (post_id, user_id, parent_id, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (post_id, g.user["id"], parent_id, content, now_str()),
        );
        db.commit();
        flash("评论发表成功！", "success");
    return redirect(url_for("post_detail", post_id=post_id) + "#comments");


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    """点赞/取消点赞：JSON 接口，前端无刷新切换。"""
    db = get_db();
    post = db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone();
    if post is None:
        abort(404);

    existing = db.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
        (g.user["id"], post_id),
    ).fetchone();
    if existing:
        db.execute(
            "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
            (g.user["id"], post_id),
        );
        liked = False;
    else:
        db.execute(
            "INSERT INTO likes (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (g.user["id"], post_id, now_str()),
        );
        liked = True;
    db.commit();
    count = db.execute(
        "SELECT COUNT(*) FROM likes WHERE post_id = ?", (post_id,)
    ).fetchone()[0];
    return jsonify({"liked": liked, "count": count});


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    """删除帖子：仅作者本人或管理员可操作。"""
    db = get_db();
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone();
    if post is None:
        abort(404);
    if post["user_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403);
    # 先解除评论的自引用，再删除帖子，避免级联删除触发外键约束。
    db.execute("UPDATE comments SET parent_id = NULL WHERE post_id = ?", (post_id,));
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,));
    db.commit();
    flash("帖子已删除！", "success");
    return redirect(url_for("index"));


@app.route("/post/<int:post_id>/pin", methods=["POST"])
@admin_required
def toggle_pin(post_id):
    """置顶/取消置顶：仅管理员可操作。"""
    db = get_db();
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone();
    if post is None:
        abort(404);
    new_value = 0 if post["is_pinned"] else 1;
    db.execute("UPDATE posts SET is_pinned = ? WHERE id = ?", (new_value, post_id));
    db.commit();
    flash("已置顶！" if new_value else "已取消置顶！", "success");
    return redirect(url_for("post_detail", post_id=post_id));


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    """删除评论：仅评论作者或管理员可操作。"""
    db = get_db();
    comment = db.execute(
        "SELECT * FROM comments WHERE id = ?", (comment_id,)
    ).fetchone();
    if comment is None:
        abort(404);
    if comment["user_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403);
    # 先解除子评论对它的引用，避免外键约束阻止删除。
    db.execute("UPDATE comments SET parent_id = NULL WHERE parent_id = ?", (comment_id,));
    db.execute("DELETE FROM comments WHERE id = ?", (comment_id,));
    db.commit();
    flash("评论已删除！", "success");
    return redirect(url_for("post_detail", post_id=comment["post_id"]) + "#comments");


# ===== 页面路由：个人中心与设置 =====
@app.route("/user/<int:user_id>")
def user_home(user_id):
    """用户主页：展示用户信息及其发布的帖子。"""
    db = get_db();
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone();
    if user is None:
        abort(404);
    posts = db.execute("""
        SELECT p.*,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count
        FROM posts p WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    """, (user_id,)).fetchall();
    comment_count = db.execute(
        "SELECT COUNT(*) FROM comments WHERE user_id = ?", (user_id,)
    ).fetchone()[0];
    return render_template(
        "user_home.html", user=user, posts=posts, comment_count=comment_count,
    );


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """个人设置：修改昵称、头像、签名与密码。"""
    db = get_db();
    if request.method == "POST":
        action = request.form.get("action");

        if action == "profile":
            # 修改基础资料。
            nickname = request.form.get("nickname", "").strip();
            avatar = request.form.get("avatar", DEFAULT_AVATAR);
            bio = request.form.get("bio", "").strip();
            if not (1 <= len(nickname) <= 20):
                flash("昵称长度需在 1-20 字之间！", "error");
            elif len(bio) > 100:
                flash("个性签名不能超过 100 字！", "error");
            else:
                if avatar not in AVATARS:
                    avatar = DEFAULT_AVATAR;
                db.execute(
                    "UPDATE users SET nickname = ?, avatar = ?, bio = ? WHERE id = ?",
                    (nickname, avatar, bio, g.user["id"]),
                );
                db.commit();
                flash("资料已更新！", "success");

        elif action == "password":
            # 修改密码：需要先验证旧密码。
            old = request.form.get("old_password", "");
            new = request.form.get("new_password", "");
            new2 = request.form.get("new_password2", "");
            if not check_password_hash(g.user["password_hash"], old):
                flash("原密码不正确！", "error");
            elif len(new) < 6:
                flash("新密码长度至少 6 位！", "error");
            elif new != new2:
                flash("两次输入的新密码不一致！", "error");
            else:
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), g.user["id"]),
                );
                db.commit();
                flash("密码修改成功！", "success");
        return redirect(url_for("settings"));

    return render_template("settings.html");


# ===== 错误页面 =====
@app.errorhandler(404)
def page_not_found(e):
    """404：页面不存在。"""
    return render_template("error.html", code=404, message="页面不存在或已被删除"), 404;


@app.errorhandler(403)
def forbidden(e):
    """403：没有操作权限。"""
    return render_template("error.html", code=403, message="你没有权限执行此操作"), 403;


# ===== 启动入口 =====
if __name__ == "__main__":
    init_db();  # 启动前确保数据表存在。
    print("——" * 50);
    print("校园论坛已启动！");
    print("请在浏览器访问：http://127.0.0.1:5000 ！");
    print("第一位注册的用户将自动成为管理员！");
    print("——" * 50);
    app.run(host="127.0.0.1", port=5000, debug=False);
    print("✅ 校园论坛服务已结束！");
