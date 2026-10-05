const path = require('path');
const express = require('express');
const session = require('express-session');
const bcrypt = require('bcryptjs');

const app = express();
const PORT = process.env.PORT || 3000;

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));
app.use(session({
  secret: process.env.SESSION_SECRET || 'dev-secret',
  resave: false,
  saveUninitialized: false,
}));

// 재시작하면 초기화되는 인메모리 데이터 (DB 붙이기 전까지 임시)
let users = [
  { id: 1, username: 'admin', passwordHash: bcrypt.hashSync('admin1234', 8), role: 'admin' },
];
let posts = [
  { id: 1, title: '공지', content: '테스트 게시판입니다.', authorId: 1, authorName: 'admin' },
];
let nextUserId = 2;
let nextPostId = 2;

function requireLogin(req, res, next) {
  if (!req.session.user) return res.redirect('/login');
  next();
}

function requireAdmin(req, res, next) {
  if (!req.session.user || req.session.user.role !== 'admin') {
    return res.status(403).send('Forbidden');
  }
  next();
}

function canModify(user, post) {
  return !!user && (user.id === post.authorId || user.role === 'admin');
}

app.get('/', (req, res) => res.redirect('/board'));

// --- auth ---
app.get('/register', (req, res) => res.render('register', { error: null }));

app.post('/register', (req, res) => {
  const { username, password } = req.body;
  if (!username || !password) {
    return res.render('register', { error: '아이디/비밀번호를 입력하세요.' });
  }
  if (users.find((u) => u.username === username)) {
    return res.render('register', { error: '이미 존재하는 아이디입니다.' });
  }
  users.push({
    id: nextUserId++,
    username,
    passwordHash: bcrypt.hashSync(password, 8),
    role: 'user',
  });
  res.redirect('/login');
});

app.get('/login', (req, res) => res.render('login', { error: null }));

app.post('/login', (req, res) => {
  const { username, password } = req.body;
  const user = users.find((u) => u.username === username);
  if (!user || !bcrypt.compareSync(password || '', user.passwordHash)) {
    return res.render('login', { error: '아이디 또는 비밀번호가 올바르지 않습니다.' });
  }
  req.session.user = { id: user.id, username: user.username, role: user.role };
  res.redirect('/board');
});

app.post('/logout', (req, res) => {
  req.session.destroy(() => res.redirect('/login'));
});

// --- board ---
app.get('/board', (req, res) => {
  res.render('board_list', { posts, user: req.session.user });
});

app.get('/board/new', requireLogin, (req, res) => {
  res.render('board_form', { post: null, user: req.session.user });
});

app.post('/board', requireLogin, (req, res) => {
  const { title, content } = req.body;
  const post = {
    id: nextPostId++,
    title: title || '(제목 없음)',
    content: content || '',
    authorId: req.session.user.id,
    authorName: req.session.user.username,
  };
  posts.push(post);
  res.redirect(`/board/${post.id}`);
});

app.get('/board/:id', (req, res) => {
  const post = posts.find((p) => p.id === Number(req.params.id));
  if (!post) return res.status(404).send('Not Found');
  res.render('board_detail', { post, user: req.session.user });
});

app.get('/board/:id/edit', requireLogin, (req, res) => {
  const post = posts.find((p) => p.id === Number(req.params.id));
  if (!post) return res.status(404).send('Not Found');
  if (!canModify(req.session.user, post)) return res.status(403).send('Forbidden');
  res.render('board_form', { post, user: req.session.user });
});

app.post('/board/:id/edit', requireLogin, (req, res) => {
  const post = posts.find((p) => p.id === Number(req.params.id));
  if (!post) return res.status(404).send('Not Found');
  if (!canModify(req.session.user, post)) return res.status(403).send('Forbidden');
  post.title = req.body.title || post.title;
  post.content = req.body.content || post.content;
  res.redirect(`/board/${post.id}`);
});

app.post('/board/:id/delete', requireLogin, (req, res) => {
  const post = posts.find((p) => p.id === Number(req.params.id));
  if (!post) return res.status(404).send('Not Found');
  if (!canModify(req.session.user, post)) return res.status(403).send('Forbidden');
  posts = posts.filter((p) => p.id !== post.id);
  res.redirect('/board');
});

// --- admin ---
app.get('/admin', requireAdmin, (req, res) => {
  res.render('admin', { users, posts, user: req.session.user });
});

app.listen(PORT, () => console.log(`Target app listening on :${PORT}`));
