// ===== 提示消息自动消失 =====
document.querySelectorAll(".flash").forEach(function (el) {
  setTimeout(function () {
    el.style.transition = "opacity .4s";
    el.style.opacity = "0";
    setTimeout(function () { el.remove(); }, 400);
  }, 3000);
});

// ===== 删除确认：带 data-confirm 属性的表单提交前弹确认框 =====
document.querySelectorAll("form[data-confirm]").forEach(function (form) {
  form.addEventListener("submit", function (e) {
    if (!confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  });
});

// ===== 点赞按钮：无刷新切换点赞状态 =====
var likeBtn = document.getElementById("like-btn");
if (likeBtn) {
  likeBtn.addEventListener("click", function () {
    // 未登录时跳转登录页
    if (likeBtn.dataset.login !== "1") {
      window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
      return;
    }
    fetch("/post/" + likeBtn.dataset.postId + "/like", { method: "POST" })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        document.getElementById("like-count").textContent = data.count;
        // 文案与配色随状态切换，图标由 CSS 控制填充。
        var label = likeBtn.querySelector(".like-label");
        if (label) { label.textContent = data.liked ? "已赞" : "点赞"; }
        likeBtn.classList.toggle("btn-liked", data.liked);
        likeBtn.classList.toggle("btn-ghost", !data.liked);
      });
  });
}

// ===== 评论回复：点击回复后设置被回复的评论 =====
var parentInput = document.getElementById("parent-id");
var replyTip = document.getElementById("reply-tip");
var replyTo = document.getElementById("reply-to");

document.querySelectorAll(".reply-btn").forEach(function (btn) {
  btn.addEventListener("click", function () {
    parentInput.value = btn.dataset.commentId;
    replyTo.textContent = "@" + btn.dataset.nickname;
    replyTip.style.display = "block";
    // 聚焦评论输入框
    document.querySelector(".comment-form textarea").focus();
  });
});

var cancelReply = document.getElementById("cancel-reply");
if (cancelReply) {
  cancelReply.addEventListener("click", function () {
    parentInput.value = "";
    replyTip.style.display = "none";
  });
}
