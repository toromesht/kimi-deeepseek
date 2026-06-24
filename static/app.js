let currentChatId = 'default';
let isStreaming = false;

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('question-input');
const sendBtn = document.getElementById('send-btn');
const newChatBtn = document.getElementById('new-chat');
const chatListEl = document.getElementById('chat-list');

// 自动调整 textarea 高度
inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

sendBtn.addEventListener('click', sendMessage);
newChatBtn.addEventListener('click', startNewChat);

function startNewChat() {
    currentChatId = 'chat_' + Date.now();
    messagesEl.innerHTML = '';
    showWelcome();
    renderChatList();
}

function showWelcome() {
    messagesEl.innerHTML = `
        <div class="welcome">
            <h2>Kimi-DeepSeek</h2>
            <p>输入问题生成代码，支持粘贴文件路径自动读取</p>
        </div>
    `;
}

function addMessage(role, content, isHtml = false) {
    const welcome = messagesEl.querySelector('.welcome');
    if (welcome) welcome.remove();

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = role === 'user' ? '你' : 'AI';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    if (isHtml) {
        bubble.innerHTML = content;
    } else {
        bubble.textContent = content;
    }

    msgDiv.appendChild(avatar);
    msgDiv.appendChild(bubble);
    messagesEl.appendChild(msgDiv);
    scrollToBottom();
    return { bubble, msgDiv };
}

function addStatusMessage() {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant';
    msgDiv.id = 'status-message';

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'AI';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.innerHTML = `
        <div class="status">
            <div class="spinner"></div>
            <span>思考中...</span>
        </div>
        <div class="step-list" id="step-list"></div>
    `;

    msgDiv.appendChild(avatar);
    msgDiv.appendChild(bubble);
    messagesEl.appendChild(msgDiv);
    scrollToBottom();
}

function updateStep(step, status, data) {
    const stepList = document.getElementById('step-list');
    if (!stepList) return;

    const names = {
        1: 'Kimi 生成提示词',
        2: 'KimiCode 写框架',
        3: 'DeepSeek 写代码',
        4: 'KimiCode 批判修复',
        5: 'DeepSeek 输出终版'
    };

    let existing = stepList.querySelector(`[data-step="${step}"]`);
    if (!existing) {
        existing = document.createElement('div');
        existing.setAttribute('data-step', step);
        stepList.appendChild(existing);
    }

    const icon = status === 'done' ? '✓' : status === 'fail' ? '✗' : '○';
    const name = names[step] || `Step ${step}`;
    existing.className = status === 'done' ? 'done' : '';
    existing.textContent = `${icon} ${name}`;

    scrollToBottom();
}

function removeStatusMessage() {
    const statusMsg = document.getElementById('status-message');
    if (statusMsg) statusMsg.remove();
}

function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function sendMessage() {
    const question = inputEl.value.trim();
    if (!question || isStreaming) return;

    inputEl.value = '';
    inputEl.style.height = 'auto';
    addMessage('user', question);
    addStatusMessage();
    isStreaming = true;
    sendBtn.disabled = true;

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, chat_id: currentChatId })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let finalCode = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = JSON.parse(line.slice(6));

                if (data.event === 'step') {
                    updateStep(data.step, data.status, data.data);
                } else if (data.event === 'done') {
                    finalCode = data.code;
                    removeStatusMessage();
                    const { bubble } = addMessage('assistant', '', true);
                    bubble.innerHTML = renderCode(finalCode);
                    if (data.paths && data.paths.length) {
                        const files = data.paths.map(p => `<div style="font-size:12px;color:#666;margin-top:8px;">💾 ${p}</div>`).join('');
                        bubble.innerHTML += files;
                    }
                    hljs.highlightAll();
                } else if (data.event === 'error') {
                    removeStatusMessage();
                    addMessage('assistant', `❌ 错误：${data.error}`, true);
                }
            }
        }
    } catch (err) {
        removeStatusMessage();
        addMessage('assistant', `❌ 请求失败：${err.message}`, true);
    } finally {
        isStreaming = false;
        sendBtn.disabled = false;
        inputEl.focus();
        renderChatList();
    }
}

function renderCode(code) {
    // 简单检测是否是纯代码块，用 markdown 渲染
    const escaped = code.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<pre><code class="language-python">${escaped}</code></pre>`;
}

function renderChatList() {
    // 简单显示当前一个对话
    chatListEl.innerHTML = '';
    const item = document.createElement('div');
    item.className = 'chat-item active';
    item.textContent = '当前对话';
    item.onclick = () => {};
    chatListEl.appendChild(item);
}

showWelcome();
renderChatList();
