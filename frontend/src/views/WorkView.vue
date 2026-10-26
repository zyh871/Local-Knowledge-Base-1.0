<template>
  <AppLayout>
    <FolderPicker
      v-if="folderPicker.open"
      :mode="folderPicker.mode"
      :start-path="folderPicker.startPath"
      :allowed-extensions="allowedExtensions"
      :budget-chars="budgetChars"
      :busy="folderPickerBusy"
      @select="onPickFolder"
      @pick="onPickLocalFile"
      @close="folderPicker.open = false"
    />
    <!-- Sidebar：工作模式下是「任务列表」 -->
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <SidebarNav mode="work" @new="newTask" />

        <div class="chat-list flex-1 min-h-0 overflow-y-auto px-3">
          <div v-if="loadingRuns" class="task-hint px-3 py-4 text-xs">加载中…</div>
          <div v-else-if="runs.length === 0" class="task-hint px-3 py-6 text-xs">
            当前工作区暂无任务。在下方输入框描述你要做的事，发送后即创建任务。
          </div>

          <div
            v-for="run in runs"
            :key="run.id"
            :class="['chat-item group flex items-start gap-2 px-3 py-2.5 cursor-pointer mb-1', { active: run.id === currentRunId }]"
            @click="selectRun(run.id)"
          >
            <span :class="['status-dot shrink-0 mt-1.5', `is-${run.status}`]" :title="statusLabel(run.status)"></span>
            <div class="flex-1 min-w-0">
              <p class="text-sm truncate chat-item-title">{{ run.title || '未命名任务' }}</p>
              <p class="task-meta text-xs truncate">
                {{ statusLabel(run.status) }} · {{ modeLabel(run.mode) }}
              </p>
            </div>
            <button
              class="task-del shrink-0 opacity-0 group-hover:opacity-100"
              title="删除任务"
              @click.stop="removeRun(run)"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          </div>
        </div>

        <UserMenu
          :name="userName"
          :theme="theme"
          :items="['models', 'theme']"
          @models="router.push('/models')"
          @theme="toggleTheme"
          @logout="logout"
        />
      </aside>
    </template>

    <!-- 主区头部 -->
    <header class="work-header flex items-center justify-between gap-3 px-4 py-4 lg:px-6 shrink-0">
      <div class="flex items-center gap-3 min-w-0">
        <h2 class="truncate font-semibold chat-header-title">
          {{ currentRun ? currentRun.title || '未命名任务' : '新建任务' }}
        </h2>
        <span v-if="currentRun" :class="['status-badge', `is-${currentRun.status}`]">
          {{ statusLabel(currentRun.status) }}
        </span>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <span v-if="errorMessage" class="error-text text-xs">{{ errorMessage }}</span>
        <span v-else-if="busy" class="stats-text text-xs">处理中…</span>
      </div>
    </header>

    <!-- 主区内容 -->
    <div class="work-body flex-1 min-h-0 overflow-y-auto px-4 py-6 lg:px-6">
      <!-- 未选中任务：空态 + 路线图 -->
      <div v-if="!currentRun" class="work-empty flex flex-col items-center justify-center h-full text-center">
        <div class="work-empty-icon w-20 h-20 flex items-center justify-center mb-6">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M9 11l3 3L22 4"/>
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
          </svg>
        </div>
        <h3 class="work-empty-title mb-2">描述你要完成的事</h3>
        <p class="work-empty-desc">
          在下面说清楚目标就行。AI 会先问几个关键问题，再给出可编辑的任务书，
          批准之后逐步执行；每一步的产出随时可以查看。
        </p>
      </div>

      <!-- 已选中任务 -->
      <div v-else class="run-detail max-w-3xl mx-auto">
        <section class="card">
          <h4 class="card-title">原始输入</h4>
          <p class="card-text">{{ currentRun.raw_input }}</p>
          <div class="card-meta">
            <span>工作区：{{ workspaceName(currentRun.workspace_id) }}</span>
            <span>执行模式：{{ modeLabel(currentRun.mode) }}</span>
            <span v-if="currentRun.force_search">联网搜索：开</span>
            <span v-if="currentRun.model">模型：{{ currentRun.model }}</span>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <h4 class="card-title">引用文件（{{ currentRun.files.length }}）</h4>
            <div class="card-head-actions">
              <button
                v-if="hasLocalFolder"
                class="mini-btn"
                title="从工作区绑定的本地文件夹里挑一个文件"
                @click="openFolderPicker('file')"
              >
                从工作区文件夹添加
              </button>
              <label class="mini-btn">
                添加文件
                <input
                  type="file"
                  multiple
                  class="hidden-input"
                  :accept="acceptAttr"
                  @change="onPickFilesForRun"
                />
              </label>
            </div>
          </div>
          <p v-if="currentRun.files.length === 0 && !folderCallout" class="card-hint">
            这些文件只作为本任务的临时上下文，不会进入文档管理、不会写入向量库。
          </p>

          <!-- ★ 最容易踩的坑：绑定文件夹 ≠ 把内容交给模型。
               用户绑好文件夹后说「整理这个文件夹里的文件」，模型却反问「哪个文件夹？」——
               因为文件根本没加进来。缺的那一步必须在界面上看得见。 -->
          <div v-if="folderCallout" class="folder-callout">
            <p class="folder-callout-text">
              工作区文件夹里已有 <strong>{{ currentRun.folder.total_files }}</strong> 个文件，
              但<strong>一个都还没加入本任务</strong> —— AI 现在看不到它们的内容。
            </p>
            <p class="folder-callout-meta">
              {{ folderCallout }}
            </p>
            <div class="folder-callout-actions">
              <button class="mini-btn primary" :disabled="busy" @click="openFolderPicker('file')">
                选择要加入的文件
              </button>
              <button
                class="mini-btn"
                :disabled="busy || !pickableFolderFiles.length"
                @click="addAllFolderFiles"
              >
                全部加入（{{ pickableFolderFiles.length }} 个）
              </button>
            </div>
          </div>

          <!-- 已有文件时：如实说明「实际进去多少」 -->
          <p v-if="fileContextSummary" class="file-context-note">
            {{ fileContextSummary }}
          </p>

          <p v-if="importError" class="import-error">
            {{ importError }}
            <button class="mini-icon-btn" title="关闭" @click="importError = ''">✕</button>
          </p>
          <div
            v-for="f in currentRun.files"
            :key="f.id"
            class="file-row flex items-center gap-2"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0 file-icon">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            <span class="flex-1 min-w-0 truncate file-name">{{ f.filename }}</span>
            <span class="file-size">{{ formatBytes(f.size_bytes) }}</span>
            <span v-if="f.parsed_chars === null" class="file-warn" title="未能解析出文本，不会参与上下文">未解析</span>
            <!-- 实际注入量：文件多时会按等额份额截取，必须如实标出来。
                 不说清楚的话，用户会以为整个文件都进了模型。 -->
            <span v-else-if="injectedLabel(f)" class="file-inject" :title="injectedTitle(f)">
              {{ injectedLabel(f) }}
            </span>
            <button class="mini-icon-btn" title="下载" @click="onDownload(f)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>
            <button class="mini-icon-btn danger" title="移除" @click="onRemoveFile(f)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6L6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </section>

        <section v-if="clarifySummary.length" class="card">
          <h4 class="card-title">澄清补充</h4>
          <ul class="clarify-summary">
            <li v-for="(line, i) in clarifySummary" :key="i">{{ line }}</li>
          </ul>
          <p v-if="currentRun.clarify.skipped" class="card-hint">（用户跳过了澄清）</p>
        </section>

        <!-- 澄清：思考流 + 问题卡 -->
        <section v-if="clarifyThinking || clarifying" class="card">
          <div class="card-head">
            <h4 class="card-title">澄清思路</h4>
            <button class="mini-btn" @click="clarifyThinkingOpen = !clarifyThinkingOpen">
              {{ clarifyThinkingOpen ? '收起' : '展开' }}
            </button>
          </div>
          <pre v-if="clarifyThinkingOpen && clarifyThinking" class="thinking-text">{{ clarifyThinking }}<span v-if="clarifying" class="caret"></span></pre>
          <p v-else-if="clarifyThinkingOpen" class="card-hint">正在等待模型输出…</p>
          <p v-else class="card-hint">已折叠（{{ clarifyThinking.length }} 字）</p>
        </section>

        <ClarifyCard
          v-if="showClarify"
          :questions="clarifyQuestions"
          :round="clarifyRound"
          :max-rounds="clarifyMax"
          :busy="busy || clarifying"
          @submit="submitClarify"
          @skip="skipClarify"
        />

        <p v-if="clarifyError" class="field-error">{{ clarifyError }}</p>

        <section v-if="thinking || planning" class="card">
          <div class="card-head">
            <h4 class="card-title">思考过程</h4>
            <button class="mini-btn" @click="thinkingOpen = !thinkingOpen">
              {{ thinkingOpen ? '收起' : '展开' }}
            </button>
          </div>
          <pre v-if="thinkingOpen && thinking" class="thinking-text">{{ thinking }}<span v-if="planning" class="caret"></span></pre>
          <p v-else-if="thinkingOpen" class="card-hint">
            正在等待模型输出…（首字延迟通常在 1-3 秒）
          </p>
          <p v-else class="card-hint">已折叠（{{ thinking.length }} 字）</p>
        </section>

        <!-- 任务书：未生成 → CTA；已生成 → 编辑器 -->
        <section v-if="!currentRun.plan" class="card">
          <div class="card-head">
            <h4 class="card-title">任务书</h4>
            <button
              class="mini-btn primary"
              :disabled="planning || !canPlan"
              @click="generatePlan"
            >
              {{ planning ? '生成中…' : '生成任务书' }}
            </button>
          </div>
          <p class="card-hint">
            模型会先说明规划思路，再给出可执行的步骤清单。
            生成后你可以直接编辑，确认无误再批准 —— 批准前不会有任何实际动作。
          </p>
          <p v-if="!canPlan" class="card-hint">
            当前状态（{{ statusLabel(currentRun.status) }}）不能生成任务书；已批准或执行中的任务请先驳回。
          </p>
          <p v-if="planError" class="field-error">{{ planError }}</p>
        </section>

        <template v-else>
          <div v-if="canPlan" class="plan-toolbar">
            <span class="card-hint">已生成任务书。若方向不对，可以直接改，也可以重新生成。</span>
            <button class="mini-btn" :disabled="planning" @click="generatePlan">
              {{ planning ? '生成中…' : '重新生成' }}
            </button>
          </div>
          <p v-if="planError" class="field-error">{{ planError }}</p>
          <p v-if="planNotice" class="plan-notice">{{ planNotice }}</p>

          <PlanEditor
            :plan="currentRun.plan"
            :issues="planIssues"
            :status="currentRun.status"
            :busy="busy || executing"
            :reject-comment="currentRun.reject_comment || ''"
            :available-tools="availableTools"
            :require-approval-tools="requireApprovalTools"
            :compact="compactPlan"
            @save="onSavePlan"
            @approve="onApprovePlan"
            @reject="onRejectPlan"
          />

          <StepTimeline
            v-if="showTimeline"
            :steps="currentRun.steps"
            :status="currentRun.status"
            :mode="currentRun.mode"
            :busy="executing || busy"
            :active-idx="activeIdx"
            :live-text="liveText"
            :next-runnable-idx="currentRun.next_runnable_idx"
            :usage-by-step="usageByStep"
            @next-step="runNextStep"
            @run-all="runAll"
            @stop="stopRun"
            @cancel="onCancelRun"
            @set-mode="onSetMode"
            @execute="executeStep"
            @approve="onApproveStep"
            @retry="onRetryStep"
            @skip="onSkipStep"
            @reject="onRejectStep"
            @download-artifact="downloadArtifactById"
          />

          <section v-if="showArtifacts" class="card">
            <div class="card-head">
              <h4 class="card-title">交付物</h4>
              <div class="card-head-actions">
                <button class="mini-btn" :disabled="busy" @click="onShareRun">
                  {{ shareUrl ? '重新生成分享链接' : '生成分享链接' }}
                </button>
                <button class="mini-btn primary" :disabled="busy" @click="onDeliver">
                  {{ delivering ? '生成中…' : artifacts.length ? '重新生成' : '生成交付物' }}
                </button>
              </div>
            </div>

            <p v-if="shareUrl" class="share-box">
              <input class="form-input" :value="shareUrl" readonly @focus="$event.target.select()" />
              <button class="mini-btn" @click="copyShareUrl">复制链接</button>
            </p>

            <ul v-if="artifacts.length" class="artifact-list">
              <li v-for="item in artifacts" :key="item.id">
                <button class="artifact-link" @click="downloadArtifact(item)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                  </svg>
                  {{ item.filename }}
                </button>
                <span class="artifact-meta">{{ formatSize(item.size_bytes) }}</span>
              </li>
            </ul>
            <p v-else class="card-hint">
              把整条流程的产出整理成一份可带走的 Markdown 文档（含结论、执行明细与参考链接）。
            </p>
          </section>

          <section v-if="currentRun.status === 'done' && currentRun.summary" class="card">
            <h4 class="card-title">结论</h4>
            <pre class="thinking-text">{{ currentRun.summary }}</pre>
          </section>

          <!-- 成本可见：没有这个数，一次运行花了多少完全是黑盒，
               也就无从判断"该优化哪一步"。数字来自服务端汇总
               （澄清 + 规划 + 各步骤），拿不到用量时整块不显示 —— 显示 0 会被读成"不花钱"。 -->
          <p v-if="usageSummaryText" class="usage-line">{{ usageSummaryText }}</p>
        </template>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-area shrink-0 px-4 pt-4 pb-5 lg:px-6">
      <div class="composer max-w-3xl mx-auto">
        <!-- 待上传文件 chip -->
        <div v-if="pendingFiles.length" class="pending-chips">
          <span v-for="(f, i) in pendingFiles" :key="`${f.name}-${i}`" class="pending-chip">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            {{ f.name }}
            <button @click="removePending(i)" aria-label="移除">×</button>
          </span>
        </div>

        <div class="composer-box">
          <textarea
            ref="textareaRef"
            v-model="draft"
            class="composer-textarea"
            rows="2"
            placeholder="今天帮你做些什么？@ 引用文件，/ 调用技能"
            @input="autogrow"
            @keydown.enter.exact.prevent="send"
          ></textarea>

          <div class="composer-toolbar">
            <button class="composer-icon-btn" title="添加文件引用" @click="pickFiles">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 5v14M5 12h14"/>
              </svg>
            </button>
            <input ref="fileInputRef" type="file" multiple class="hidden-input" @change="onPickFiles" />

            <div class="composer-right">
              <!-- 需求澄清 -->
              <button
                class="composer-chip"
                data-role="clarify-chip"
                :class="{ on: clarifyEnabled }"
                title="开启后 AI 会先问几个关键问题，再生成任务书"
                @click="toggleClarify"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"/>
                  <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
                  <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                需求澄清
              </button>

              <!-- 模型 -->
              <div class="menu-anchor">
                <!-- data-role：给界面验证脚本一个稳定锚点。按文案定位会在改词后失效 -->
                <button
                  class="composer-chip"
                  data-role="model-chip"
                  :class="{ on: openMenu === 'model' }"
                  :title="currentModelHint"
                  @click.stop="toggleMenu('model')"
                >
                  {{ currentModelLabel }}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"/>
                  </svg>
                </button>
                <div v-if="openMenu === 'model'" class="menu" @click.stop>
                  <div v-if="models.length === 0" class="menu-empty">暂无可用模型</div>
                  <button
                    v-for="m in models"
                    :key="m.id"
                    :class="['menu-item', { active: m.id === selectedModel }]"
                    @click="selectModel(m.id)"
                  >
                    <span class="menu-item-label">{{ m.label || m.id }}</span>
                    <span v-if="m.custom" class="menu-item-tag custom">自定义</span>
                    <span v-if="m.is_default" class="menu-item-tag">默认</span>
                  </button>
                  <button class="menu-manage" @click="router.push('/models')">
                    管理模型…
                  </button>
                </div>
              </div>

              <!-- 联网搜索 -->
              <button
                class="composer-chip"
                data-role="search-chip"
                :class="{ on: forceSearch }"
                title="开启后本任务会显式联网搜索，并返回可访问的来源链接"
                @click="toggleSearch"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"/>
                  <line x1="2" y1="12" x2="22" y2="12"/>
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                </svg>
                智能搜索
              </button>

              <button class="composer-send" :disabled="!canSend" aria-label="发送" @click="send">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 19V5"/>
                  <path d="M5 12l7-7 7 7"/>
                </svg>
              </button>
            </div>
          </div>
        </div>

        <!-- 输入框下方：工作空间 / 执行权限 -->
        <div class="composer-meta">
          <div class="menu-anchor">
            <button class="composer-meta-btn" :class="{ on: openMenu === 'workspace' }" @click.stop="toggleMenu('workspace')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              </svg>
              {{ currentWorkspace ? currentWorkspace.name : '选择工作空间' }}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </button>

            <div v-if="openMenu === 'workspace'" class="menu menu-wide" @click.stop>
              <div v-for="w in workspaces" :key="w.id" class="menu-row">
                <template v-if="renamingId === w.id">
                  <input
                    v-model="renamingName"
                    class="menu-input"
                    @keydown.enter="submitRename"
                    @keydown.esc="cancelRename"
                  />
                  <button class="mini-btn" @click="submitRename">保存</button>
                </template>
                <template v-else>
                  <button
                    :class="['menu-item', 'flex-1', { active: w.id === currentWorkspaceId }]"
                    @click="selectWorkspace(w.id)"
                  >
                    <span class="menu-item-label">{{ w.name }}</span>
                    <span class="menu-item-tag">{{ w.run_count }} 任务</span>
                  </button>
                  <button class="mini-icon-btn" title="重命名" @click.stop="startRename(w)">改</button>
                  <button class="mini-icon-btn danger" title="删除工作区" @click.stop="removeWorkspace(w)">删</button>
                </template>
              </div>

              <div class="menu-divider"></div>

              <!-- 本地文件夹：绑一个本机目录当工作区，不必把已有文件重传一遍 -->
              <div class="menu-folder">
                <p class="menu-folder-head">
                  <span>本地文件夹</span>
                  <span
                    v-if="currentWorkspace && currentWorkspace.local_dir"
                    class="menu-folder-path"
                    :title="currentWorkspace.local_dir"
                  >{{ currentWorkspace.local_dir }}</span>
                  <span v-else class="menu-folder-empty">未绑定</span>
                </p>
                <div class="menu-row">
                  <button class="mini-btn" @click.stop="openFolderPicker('folder')">
                    {{ currentWorkspace && currentWorkspace.local_dir ? '更换…' : '选择…' }}
                  </button>
                  <button
                    class="mini-btn"
                    :disabled="!hasLocalFolder"
                    title="在系统文件管理器里打开"
                    @click.stop="onOpenFolder"
                  >
                    打开
                  </button>
                  <button
                    v-if="currentWorkspace && currentWorkspace.local_dir"
                    class="mini-btn"
                    @click.stop="onUnbindFolder"
                  >
                    解绑
                  </button>
                </div>
              </div>

              <div class="menu-divider"></div>
              <div class="menu-row">
                <input
                  v-model="newWorkspaceName"
                  class="menu-input"
                  placeholder="新建工作区名称"
                  @keydown.enter="submitCreateWorkspace"
                />
                <button class="mini-btn" :disabled="!newWorkspaceName.trim()" @click="submitCreateWorkspace">
                  新建
                </button>
              </div>
            </div>
          </div>

          <div class="menu-anchor">
            <button class="composer-meta-btn" :class="{ on: openMenu === 'mode' }" @click.stop="toggleMenu('mode')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              </svg>
              {{ modeLabel(mode) }}
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </button>
            <div v-if="openMenu === 'mode'" class="menu menu-up" @click.stop>
              <button
                v-for="m in modes"
                :key="m.value"
                :class="['menu-item', { active: m.value === mode }]"
                @click="setMode(m.value)"
              >
                <span class="menu-item-label">{{ m.label }}</span>
              </button>
              <div class="menu-note">
                无论选哪种，能执行代码/写文件的步骤都必须单独审批；
                已创建的任务可在详情里随时切换。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import BrandTitle from '../components/BrandTitle.vue'
import SidebarNav from '../components/sidebar/SidebarNav.vue'
import UserMenu from '../components/sidebar/UserMenu.vue'
import PlanEditor from '../components/workflow/PlanEditor.vue'
import StepTimeline from '../components/workflow/StepTimeline.vue'
import ClarifyCard from '../components/workflow/ClarifyCard.vue'
import FolderPicker from '../components/workflow/FolderPicker.vue'
import { useTheme } from '../composables/useTheme'
import {
  workflowApi,
  streamPlan,
  streamClarify,
  streamExecuteStep,
  downloadRunFile,
  RUN_STATUS_LABELS,
} from '../api/workflow'

const router = useRouter()
const { theme, toggleTheme } = useTheme()
const userName = ref(localStorage.getItem('username') || '')

const MODE_LABELS = { manual: '人工审查', auto: 'AI 自动执行' }

// ── 状态 ──────────────────────────────────────────────────────────
const capabilities = ref(null)
const workspaces = ref([])
const currentWorkspaceId = ref(localStorage.getItem('workflow:workspace') || '')
const runs = ref([])
const currentRunId = ref('')
const currentRun = ref(null)
const loadingRuns = ref(false)
const busy = ref(false)
const errorMessage = ref('')
/** 批量引用里被跳过的文件（单独一条，避免与普通错误混在一起被忽略）。 */
const importError = ref('')

const draft = ref('')
const pendingFiles = ref([])
const textareaRef = ref(null)
const fileInputRef = ref(null)

const mode = ref(localStorage.getItem('workflow:mode') || 'manual')
const forceSearch = ref(localStorage.getItem('workflow:forceSearch') === '1')

const models = ref([])
const selectedModel = ref(localStorage.getItem('preferredModel:work') || '')

const openMenu = ref(null)
const newWorkspaceName = ref('')
const renamingId = ref('')
const renamingName = ref('')

// ── 任务书（P2）───────────────────────────────────────────────────
const planning = ref(false)
const thinking = ref('')
const thinkingOpen = ref(true)
const planIssues = ref([])
const planError = ref('')
//: 一次性的操作提示（例如「已保留前 1 步的产出」）
const planNotice = ref('')

// ── 澄清（P4）─────────────────────────────────────────────────────
const clarifying = ref(false)
const clarifyThinking = ref('')
const clarifyThinkingOpen = ref(true)
const clarifyQuestions = ref([])
const clarifyRound = ref(0)
const clarifyMax = ref(3)
const clarifyError = ref('')
//: 输入区的「需求澄清」开关，按模式分别记忆
const clarifyEnabled = ref(localStorage.getItem('workflow:clarify') !== '0')

// ── 交付物与分享（P7）─────────────────────────────────────────────
const artifacts = ref([])
const delivering = ref(false)
const shareUrl = ref('')

// ── 步骤执行（P3）─────────────────────────────────────────────────
const executing = ref(false)
const activeIdx = ref(0)
const liveText = ref('')
const autoRunning = ref(false)
let execAbort = null
let stopRequested = false

/** 与后端 `WorkflowService._PLANNABLE_STATUSES` 保持一致。 */
const PLANNABLE_STATUSES = ['draft', 'clarifying', 'planning', 'awaiting_approval', 'paused', 'failed']

/** 出现的时机：任务书已批准（或已在执行/已结束）——此时才有步骤可跑。 */
const EXECUTION_STATUSES = ['approved', 'running', 'done', 'paused', 'failed', 'cancelled']

/** 步骤被真正推进过的状态。 */
const STEP_ACTIVE_STATUSES = ['running', 'done', 'failed', 'skipped']

// ── 计算属性 ──────────────────────────────────────────────────────
const currentWorkspace = computed(
  () => workspaces.value.find((w) => w.id === currentWorkspaceId.value) || null
)
const modes = computed(() => capabilities.value?.modes || [
  { value: 'manual', label: '人工审查' },
  { value: 'auto', label: 'AI 自动执行' },
])
const availableTools = computed(() => capabilities.value?.tools || [])
const requireApprovalTools = computed(
  () => capabilities.value?.require_approval_tools || []
)
const canPlan = computed(
  () => !!currentRun.value && PLANNABLE_STATUSES.includes(currentRun.value.status)
)
/**
 * 执行是否**已经开始过**。
 *
 * 这是「显示时间线」与「任务书折叠成紧凑态」的真正判据 —— 只看 run.status 会漏掉
 * 一个关键场景：**驳回重规划后状态回到 `awaiting_approval`**，
 * 此时前序步骤已经跑完、产出也被保留了，但如果按状态判断就会把整条时间线藏起来，
 * 用户会以为之前的产出没了。而这恰恰是 P5 最不该出的错。
 */
const executionStarted = computed(() =>
  (currentRun.value?.steps || []).some((s) => STEP_ACTIVE_STATUSES.includes(s.status))
)

const showTimeline = computed(
  () =>
    !!currentRun.value?.plan &&
    (executionStarted.value ||
      EXECUTION_STATUSES.includes(currentRun.value.status))
)

const compactPlan = computed(
  () =>
    !!currentRun.value &&
    (executionStarted.value ||
      EXECUTION_STATUSES.includes(currentRun.value.status))
)

/** 至少推进过一步才值得谈「交付物」—— 空文档对用户没有价值。 */
const showArtifacts = computed(() => executionStarted.value)

/** 有待回答的澄清问题时才展示问题卡。 */
const showClarify = computed(() => clarifyQuestions.value.length > 0)

/** 「原话 → 澄清补充 → 任务书」三段对比的中间那段。 */
const clarifySummary = computed(() => {
  const rounds = currentRun.value?.clarify?.rounds || []
  const lines = []
  for (const round of rounds) {
    for (const question of round.questions || []) {
      const answer = (round.answers || {})[question.id]
      if (!answer) continue
      const text = (answer.text || '').trim()
      let label = text
      if (!text && answer.value) {
        const hit = (question.options || []).find((o) => o.value === answer.value)
        label = hit ? hit.label : answer.value
      }
      if (label) lines.push(`· ${question.question} → ${label}`)
    }
  }
  return lines
})
// 只显示模型 id：工作模式的输入区还有「智能搜索」「发送」两个控件，
// 放长描述会把输入区挤满。完整说明交给 tooltip。
/**
 * 模型 chip 上显示的文字。
 *
 * ⚠️ 必须查 label，不能直接回显 id：内置模型的 id 本身就是可读名（`glm-4-flash`），
 * 所以直接回显 id 一直「看起来没问题」；自定义模型的 id 是 `custom:me_xxx`，
 * 直接回显就是一串机器码。
 */
const currentModelLabel = computed(() => {
  if (!selectedModel.value) return '默认模型'
  const hit = models.value.find((m) => m.id === selectedModel.value)
  if (!hit) return selectedModel.value
  return hit.label || hit.id
})
const currentModelHint = computed(() => {
  const hit = models.value.find((m) => m.id === selectedModel.value)
  return hit ? hit.label || hit.id : ''
})
const canSend = computed(
  () => !busy.value && !!currentWorkspaceId.value && draft.value.trim().length > 0
)

// ── 展示辅助 ──────────────────────────────────────────────────────
function statusLabel(status) {
  return RUN_STATUS_LABELS[status] || status
}
function modeLabel(value) {
  return MODE_LABELS[value] || value
}
function workspaceName(id) {
  return workspaces.value.find((w) => w.id === id)?.name || '—'
}
function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// ── 初始化 ────────────────────────────────────────────────────────
async function bootstrap() {
  try {
    capabilities.value = await workflowApi.capabilities()
  } catch (e) {
    errorMessage.value = e.message
  }

  await Promise.all([loadWorkspaces(), loadModels()])
  await loadRuns()
}

async function loadWorkspaces() {
  try {
    const data = await workflowApi.listWorkspaces()
    workspaces.value = data.workspaces || []

    // 一个都没有时自动建一个默认工作区 —— 否则用户第一步就卡在空下拉里
    if (workspaces.value.length === 0) {
      const created = await workflowApi.createWorkspace('默认工作区')
      workspaces.value = [created]
    }

    const stillExists = workspaces.value.some((w) => w.id === currentWorkspaceId.value)
    if (!stillExists) {
      currentWorkspaceId.value = workspaces.value[0].id
      persist('workflow:workspace', currentWorkspaceId.value)
    }
  } catch (e) {
    errorMessage.value = e.message
  }
}

async function loadModels() {
  try {
    const data = await workflowApi.listModels()
    models.value = data.models || []
    const ids = models.value.map((m) => m.id)
    // 白名单变了（例如换了后端）→ 回落到服务端默认，不静默保留非法值
    if (!selectedModel.value || !ids.includes(selectedModel.value)) {
      selectedModel.value = data.default_model || ids[0] || ''
      persist('preferredModel:work', selectedModel.value)
    }
  } catch (e) {
    // 模型清单拿不到不应阻塞任务列表，用户仍可创建任务（走服务端默认模型）
    console.warn('获取模型列表失败:', e)
  }
}

async function loadRuns() {
  if (!currentWorkspaceId.value) return
  loadingRuns.value = true
  try {
    const data = await workflowApi.listRuns({ workspace_id: currentWorkspaceId.value })
    runs.value = data.runs || []
  } catch (e) {
    errorMessage.value = e.message
  } finally {
    loadingRuns.value = false
  }
}

// ── 本地持久化 ────────────────────────────────────────────────────
function persist(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch (e) {
    // 隐私模式下 localStorage 可能不可用，失败不影响功能
  }
}

// ── 菜单 ──────────────────────────────────────────────────────────
function toggleMenu(name) {
  openMenu.value = openMenu.value === name ? null : name
}
function closeAllMenus() {
  openMenu.value = null
}
onMounted(() => {
  document.addEventListener('click', closeAllMenus)
  bootstrap()
})
onUnmounted(() => {
  document.removeEventListener('click', closeAllMenus)
})

// ── 工作区操作 ────────────────────────────────────────────────────
async function selectWorkspace(id) {
  currentWorkspaceId.value = id
  persist('workflow:workspace', id)
  closeAllMenus()
  currentRunId.value = ''
  currentRun.value = null
  await loadRuns()
}

async function submitCreateWorkspace() {
  const name = newWorkspaceName.value.trim()
  if (!name) return
  await guard(async () => {
    const created = await workflowApi.createWorkspace(name)
    newWorkspaceName.value = ''
    await loadWorkspaces()
    await selectWorkspace(created.id)
  })
}

function startRename(workspace) {
  renamingId.value = workspace.id
  renamingName.value = workspace.name
}
function cancelRename() {
  renamingId.value = ''
  renamingName.value = ''
}
async function submitRename() {
  const name = renamingName.value.trim()
  if (!name || !renamingId.value) return
  await guard(async () => {
    await workflowApi.renameWorkspace(renamingId.value, name)
    cancelRename()
    await loadWorkspaces()
  })
}

async function removeWorkspace(workspace) {
  if (!window.confirm(`删除工作区「${workspace.name}」？其下任务与引用文件会一起删除。`)) return
  await guard(async () => {
    await workflowApi.deleteWorkspace(workspace.id)
    if (currentWorkspaceId.value === workspace.id) {
      currentWorkspaceId.value = ''
      currentRunId.value = ''
      currentRun.value = null
    }
    await loadWorkspaces()
    await loadRuns()
  })
}

// ── 任务操作 ──────────────────────────────────────────────────────
function newTask() {
  currentRunId.value = ''
  currentRun.value = null
  draft.value = ''
  pendingFiles.value = []
  errorMessage.value = ''
  resetPlanState()
  textareaRef.value?.focus()
}

async function selectRun(id) {
  if (id === currentRunId.value) return
  resetPlanState()
  await guard(async () => {
    currentRunId.value = id
    currentRun.value = await workflowApi.getRun(id)
  })
}

async function removeRun(run) {
  if (!window.confirm(`删除任务「${run.title || '未命名任务'}」？`)) return
  await guard(async () => {
    await workflowApi.deleteRun(run.id)
    if (currentRunId.value === run.id) newTask()
    await loadRuns()
  })
}

// ── 附件 ──────────────────────────────────────────────────────────
function pickFiles() {
  fileInputRef.value?.click()
}
function onPickFiles(event) {
  const files = Array.from(event.target.files || [])
  pendingFiles.value = [...pendingFiles.value, ...files]
  event.target.value = '' // 允许重复选择同一文件
}
function removePending(index) {
  pendingFiles.value = pendingFiles.value.filter((_, i) => i !== index)
}

async function onPickFilesForRun(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length || !currentRun.value) return
  await guard(async () => {
    const data = await workflowApi.uploadFiles(currentRun.value.id, files)
    reportWarnings(data.warnings)
    currentRun.value = await workflowApi.getRun(currentRun.value.id)
  })
}

async function onRemoveFile(file) {
  if (!currentRun.value) return
  await guard(async () => {
    await workflowApi.deleteFile(currentRun.value.id, file.id)
    currentRun.value = await workflowApi.getRun(currentRun.value.id)
  })
}

// ── 本地文件夹 ────────────────────────────────────────────────────
const folderPicker = reactive({ open: false, mode: 'folder', startPath: '' })

/**
 * 选择器专用的忙标志。
 *
 * ⚠️ 不能把页面全局的 `busy` 传进选择器：那是所有 guard() 操作共用的一个标志，
 * 任何无关操作（比如刚发完消息正在刷新）进行中，选择器里**所有条目都会点不动** ——
 * 用户完全看不出为什么。忙标志要覆盖的范围，应当与它保护的操作一致。
 */
const folderPickerBusy = ref(false)

/** 每步的 token 用量按 idx 索引（服务端已归一化，前端不重复实现）。 */
const usageByStep = computed(() => {
  const map = {}
  for (const item of currentRun.value?.usage?.steps || []) {
    map[item.idx] = item
  }
  return map
})

/**
 * 本任务的 token 用量汇总。
 *
 * ⚠️ 只统计**真正拿到用量**的调用（`calls`），并把次数一起写出来 ——
 * 上游没返回 usage 时数字会偏小，读者需要知道这个数覆盖了几次调用。
 */
const usageSummaryText = computed(() => {
  const usage = currentRun.value?.usage
  if (!usage || !usage.total_tokens) return ''
  const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
  return (
    `本任务 ${usage.calls} 次模型调用 · 合计 ${fmt(usage.total_tokens)} tokens` +
    `（输入 ${fmt(usage.input_tokens)} / 输出 ${fmt(usage.output_tokens)}）`
  )
})

/** 后端允许引用的文件类型（来自 capabilities）。 */
const allowedExtensions = computed(() => capabilities.value?.allowed_extensions || [])
/** 给 <input type="file"> 的 accept：让不可引用的文件在系统对话框里就选不中。 */
const acceptAttr = computed(() => allowedExtensions.value.join(','))
/** 上下文预算（字符）—— 用来如实告知「选多了每个分到多少」。 */
const budgetChars = computed(() => capabilities.value?.file_inline_max_chars || 8000)

function canImport(name) {
  if (!allowedExtensions.value.length) return true
  const dot = name.lastIndexOf('.')
  return dot >= 0 && allowedExtensions.value.includes(name.slice(dot).toLowerCase())
}

/** 工作区文件夹里**可引用**的文件（用于「全部加入」）。 */
const pickableFolderFiles = computed(() =>
  (currentRun.value?.folder?.files || []).filter((f) => canImport(f.name))
)

/**
 * 「绑定文件夹但一个文件都没加」时的提示文案。
 *
 * 这是用户最常卡住的地方：绑定 ≠ 交给模型。没有这段提示，
 * 用户会以为「程序访问不了我的本地文件」（真实发生过的误判）。
 */
const folderCallout = computed(() => {
  const folder = currentRun.value?.folder
  if (!folder || !folder.total_files) return ''
  if ((currentRun.value?.files || []).length) return ''
  const total = Number(folder.total_bytes) || 0
  const size = total < 1024 * 1024 ? `${(total / 1024).toFixed(0)} KB` : `${(total / 1024 / 1024).toFixed(1)} MB`
  const usable = pickableFolderFiles.value.length
  const skipped = folder.total_files - usable
  return (
    `共 ${folder.total_files} 个文件、约 ${size}；` +
    (skipped > 0 ? `其中 ${skipped} 个类型不支持引用。` : '') +
    `可引用 ${usable} 个。`
  )
})

/** 已有的引用文件「实际会进去多少」的汇总（数字来自服务端，与执行时同一套逻辑）。 */
const fileContextSummary = computed(() => {
  const ctx = currentRun.value?.file_context
  if (!ctx) return ''
  const fmt = (n) => (n >= 10000 ? `${(n / 1000).toFixed(1)} 千` : String(n))
  const count = (ctx.notes || []).filter((n) => n.mode !== 'empty').length
  if (!count) return ''
  if (ctx.injected_chars >= ctx.total_chars) {
    return `${count} 个文件共 ${fmt(ctx.total_chars)} 字，全部进入上下文。`
  }
  return (
    `${count} 个文件共 ${fmt(ctx.total_chars)} 字 —— 超出了 ` +
    `${ctx.budget_chars.toLocaleString()} 字的上下文预算，` +
    `实际只会注入约 ${fmt(ctx.injected_chars)} 字（每个文件约 ${Math.round(
      ctx.injected_chars / count
    )} 字）。需要全文时请减少文件数量。`
  )
})

function injectedNote(file) {
  const notes = currentRun.value?.file_context?.notes || []
  return notes.find((n) => n.id === file.id) || null
}

/** 单个文件的注入量标签；全文进上下文时不显示（没信息量）。 */
function injectedLabel(file) {
  const note = injectedNote(file)
  if (!note || !note.chars) return ''
  if (note.mode === 'full') return ''
  return `注入约 ${note.injected_chars} 字（${Math.round(
    (note.injected_chars / note.chars) * 100
  )}%）`
}

function injectedTitle(file) {
  const note = injectedNote(file)
  if (!note) return ''
  return `该文件解析出 ${note.chars} 字，本次实际注入约 ${note.injected_chars} 字`
}

/** 一键把工作区文件夹里所有可引用文件加进本任务。 */
async function addAllFolderFiles() {
  if (!currentRun.value) return
  const paths = pickableFolderFiles.value.map((f) => f.path)
  if (!paths.length) return
  await importLocalFiles(paths)
}

async function importLocalFiles(paths) {
  if (!currentRun.value || !paths.length) return
  await guard(async () => {
    folderPickerBusy.value = true
    try {
      const res = await workflowApi.importLocalFiles(currentRun.value.id, paths)
      folderPicker.open = false
      reportWarnings(res.warnings)
      reportImportFailures(res.failed)
      currentRun.value = await workflowApi.getRun(currentRun.value.id)
    } finally {
      folderPickerBusy.value = false
    }
  })
}

/** 批量引用里被跳过的文件必须点名，否则用户以为全都加上了。 */
function reportImportFailures(failed) {
  const list = failed || []
  if (!list.length) return
  const detail = list
    .slice(0, 3)
    .map((f) => `${f.name}（${f.reason}）`)
    .join('；')
  const more = list.length > 3 ? ` 等 ${list.length} 个` : ''
  importError.value = `以下文件未加入：${detail}${more}`
}

/** 当前工作区是否绑定了本地文件夹（决定「从工作区文件夹添加」是否出现）。 */
const hasLocalFolder = computed(() => !!currentWorkspace.value?.local_dir)

function openFolderPicker(mode) {
  folderPicker.mode = mode
  folderPicker.startPath = currentWorkspace.value?.local_dir || ''
  folderPicker.open = true
  openMenu.value = null
}

/** 就地更新工作区对象，避免整表重取导致菜单闪一下。 */
function patchWorkspace(updated) {
  const index = workspaces.value.findIndex((w) => w.id === updated.id)
  if (index >= 0) workspaces.value[index] = { ...workspaces.value[index], ...updated }
}

async function onPickFolder(path) {
  await guard(async () => {
    folderPickerBusy.value = true
    try {
      const updated = await workflowApi.bindWorkspaceFolder(currentWorkspaceId.value, path)
      patchWorkspace(updated)
      folderPicker.open = false
    } finally {
      folderPickerBusy.value = false
    }
  })
}

async function onUnbindFolder() {
  await guard(async () => {
    const updated = await workflowApi.bindWorkspaceFolder(currentWorkspaceId.value, '')
    patchWorkspace(updated)
  })
}

async function onOpenFolder() {
  await guard(async () => {
    await workflowApi.openWorkspaceFolder(currentWorkspaceId.value)
  })
}

/**
 * 从绑定文件夹里引用文件到当前任务（选择器多选后提交，收到的是路径数组）。
 *
 * 「选好即加」：用户点「加入选中的 N 个文件」就是想加，不再多要一次确认。
 */
async function onPickLocalFile(paths) {
  await importLocalFiles(Array.isArray(paths) ? paths : [paths])
}

async function onDownload(file) {
  await guard(async () => {
    await downloadRunFile(file.id, file.filename)
  })
}

function reportWarnings(warnings) {
  if (warnings && warnings.length) {
    // 解析失败的告警必须让用户看见 —— 否则他会以为文件已经进了上下文
    errorMessage.value = warnings.join('；')
  }
}

// ── 发送 ──────────────────────────────────────────────────────────
async function send() {
  if (!canSend.value) return
  const text = draft.value.trim()
  const files = pendingFiles.value

  await guard(async () => {
    const created = await workflowApi.createRun({
      workspace_id: currentWorkspaceId.value,
      raw_input: text,
      mode: mode.value,
      model: selectedModel.value || undefined,
      force_search: forceSearch.value,
    })

    // 先建任务再传文件：run_files 需要 run_id，
    // 但用户期望的是「先挂文件再发送」，所以两次请求由一次操作串起来
    if (files.length) {
      const uploaded = await workflowApi.uploadFiles(created.id, files)
      reportWarnings(uploaded.warnings)
    }

    draft.value = ''
    pendingFiles.value = []
    currentRunId.value = created.id
    await loadRuns()
    currentRun.value = await workflowApi.getRun(created.id)

    // 需求澄清默认开启：先问几个关键问题，再生成任务书。
    // 关掉它就等于「跳过澄清」——直接让用户去点「生成任务书」。
    if (clarifyEnabled.value) {
      await startClarify()
    }

    // 输入框回到单行高度
    requestAnimationFrame(() => {
      if (textareaRef.value) textareaRef.value.style.height = 'auto'
    })
  })
}

// ── 模式 / 模型 / 搜索 ────────────────────────────────────────────
async function setMode(value) {
  mode.value = value
  persist('workflow:mode', value)
  closeAllMenus()
  // 已打开的任务：同步改服务端，让「执行中切换」立刻对未开始的步骤生效
  if (currentRun.value && currentRun.value.mode !== value) {
    await guard(async () => {
      currentRun.value = await workflowApi.updateRun(currentRun.value.id, { mode: value })
      await loadRuns()
    })
  }
}

function selectModel(id) {
  selectedModel.value = id
  persist('preferredModel:work', id)
  closeAllMenus()
}

function toggleSearch() {
  forceSearch.value = !forceSearch.value
  persist('workflow:forceSearch', forceSearch.value ? '1' : '0')
}

function autogrow(event) {
  const el = event.target
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}

// ── 澄清 ──────────────────────────────────────────────────────────
function resetClarifyState() {
  clarifyThinking.value = ''
  clarifyThinkingOpen.value = true
  clarifyQuestions.value = []
  clarifyRound.value = 0
  clarifyError.value = ''
}

/**
 * 产出一轮澄清问题。
 *
 * 模型判定「信息已足够」时会返回空问题数组 —— 此时不再追问，直接转入规划。
 */
async function startClarify() {
  if (!currentRun.value || clarifying.value) return
  clarifying.value = true
  clarifyError.value = ''
  clarifyThinking.value = ''
  const runId = currentRun.value.id
  let sufficient = false
  let asked = 0
  try {
    await streamClarify(runId, {
      onEvent: (event) => {
        const content = event.content || {}
        if (event.type === 'thinking_delta') {
          clarifyThinking.value += content.text || ''
        } else if (event.type === 'clarify_questions') {
          clarifyQuestions.value = content.questions || []
        } else if (event.type === 'final') {
          if (content.ok === false) {
            clarifyError.value = content.error || '生成澄清问题失败'
          } else {
            clarifyQuestions.value = content.questions || []
            clarifyRound.value = content.round || 0
            clarifyMax.value = content.max_rounds || clarifyMax.value
            sufficient = !!content.sufficient
            asked = (content.questions || []).length
          }
        }
      },
    })
  } catch (e) {
    clarifyError.value = e.message || '生成澄清问题失败'
  } finally {
    clarifying.value = false
    await refreshCurrentRun()
    await loadRuns()
  }

  // 模型判定「信息已足够」且没有新问题 → 直接进规划。
  // 少了这一步，首轮就判足够的任务会停在空白页：不提问、也不生成任务书。
  if (sufficient && !asked && !clarifyError.value && canPlan.value) {
    await generatePlan()
  }
}

async function submitClarify(answers) {
  await guard(async () => {
    const res = await workflowApi.answerClarify(currentRun.value.id, answers)
    currentRun.value = res.run
    clarifyQuestions.value = []

    const rounds = currentRun.value.clarify?.rounds?.length || 0
    const max = clarifyMax.value || 3
    const sufficient = !!currentRun.value.clarify?.sufficient
    await loadRuns()

    if (sufficient || rounds >= max) {
      // 信息够了 / 轮次到顶 → 直接进规划，不再让用户多点一次
      await generatePlan()
    } else {
      await startClarify()
    }
  })
}

async function skipClarify() {
  await guard(async () => {
    const res = await workflowApi.skipClarify(currentRun.value.id, '')
    currentRun.value = res.run
    clarifyQuestions.value = []
    resetClarifyState()
    await loadRuns()
    await generatePlan()
  })
}

function toggleClarify() {
  clarifyEnabled.value = !clarifyEnabled.value
  persist('workflow:clarify', clarifyEnabled.value ? '1' : '0')
}

// ── 任务书 ────────────────────────────────────────────────────────
function resetPlanState() {
  thinking.value = ''
  thinkingOpen.value = true
  planIssues.value = []
  planError.value = ''
  planNotice.value = ''
  resetClarifyState()
  // 执行态也要一起复位：否则切换任务时会把上一个任务的实时输出带过来
  liveText.value = ''
  activeIdx.value = 0
  artifacts.value = []
  shareUrl.value = ''
  if (execAbort) {
    execAbort.abort()
    execAbort = null
  }
}

async function refreshCurrentRun() {
  if (!currentRunId.value) return
  try {
    currentRun.value = await workflowApi.getRun(currentRunId.value)
    await loadArtifacts()
  } catch (e) {
    // 刷新失败不应覆盖已有的错误提示（生成失败的原因更重要）
    console.warn('刷新任务详情失败:', e)
  }
}

async function generatePlan() {
  if (!currentRun.value || planning.value) return
  planning.value = true
  resetPlanState()
  const runId = currentRun.value.id
  try {
    await streamPlan(runId, {
      onEvent: (event) => {
        const content = event.content || {}
        if (event.type === 'thinking_delta') {
          thinking.value += content.text || ''
        } else if (event.type === 'plan_ready') {
          planIssues.value = content.issues || []
        } else if (event.type === 'final') {
          if (content.ok === false) {
            planError.value = content.error || '生成任务书失败'
          } else {
            planIssues.value = content.issues || planIssues.value
          }
        }
      },
    })
  } catch (e) {
    planError.value = e.message || '生成任务书失败'
  } finally {
    planning.value = false
    await refreshCurrentRun()
    await loadRuns()
  }
}

async function onSavePlan(plan) {
  await guard(async () => {
    const res = await workflowApi.savePlan(currentRun.value.id, plan)
    currentRun.value = res.run
    // 服务端可能修正了用户的手改内容，必须把修正回显出来
    planIssues.value = res.issues || []
    planError.value = ''
    await loadRuns()
  })
}

async function onApprovePlan() {
  await guard(async () => {
    const res = await workflowApi.approvePlan(currentRun.value.id)
    currentRun.value = res.run
    await loadRuns()
  })
}

async function onRejectPlan(comment) {
  await guard(async () => {
    const res = await workflowApi.rejectPlan(currentRun.value.id, comment)
    currentRun.value = res.run
    planError.value = ''
    await loadRuns()
  })
}

// ── 步骤执行 ──────────────────────────────────────────────────────
/**
 * 执行一个步骤。
 *
 * 每次只推进一步（服务端也是如此设计）—— 因此「自动执行剩余」是在客户端
 * 循环调用它，而不是一个长连接跑到底。好处是每一步之间都能停、能看、能改。
 *
 * @returns {Promise<boolean>} 是否成功完成该步
 */
async function executeStep(idx) {
  if (!currentRun.value || executing.value) return false
  executing.value = true
  activeIdx.value = idx
  liveText.value = ''
  errorMessage.value = ''
  const runId = currentRun.value.id
  let ok = false
  execAbort = new AbortController()

  // 乐观置为「运行中」。
  //
  // ⚠️ 执行期间前端**不会**再拉取任务状态（只在结束后刷新一次），
  // 所以不这样做的话，整个执行过程里步骤一直显示「待执行」：
  // 时间线上看不到进度、新加的已用时秒表也永远不出现 ——
  // 而等待中的用户最需要的恰恰是这段反馈。
  // 真正的状态永远以服务端为准：结束后的 refreshCurrentRun() 会把它纠正回来。
  const target = currentRun.value.steps?.find((s) => s.idx === idx)
  if (target) {
    target.status = 'running'
    target.started_at = target.started_at || Date.now() / 1000
  }

  try {
    await streamExecuteStep(runId, idx, {
      signal: execAbort.signal,
      onEvent: (event) => {
        const content = event.content || {}
        if (event.type === 'step_start') {
          activeIdx.value = content.idx || idx
        } else if (event.type === 'step_delta') {
          liveText.value += content.text || ''
        } else if (event.type === 'final') {
          ok = content.ok === true
          if (!ok) errorMessage.value = content.error || '步骤执行失败'
        }
      },
    })
  } catch (e) {
    // 主动停止会抛 AbortError，不算错误
    if (e.name !== 'AbortError') errorMessage.value = e.message || '步骤执行失败'
  } finally {
    executing.value = false
    execAbort = null
    activeIdx.value = 0
    await refreshCurrentRun()
    await loadRuns()
    // 输出已落库，实时文本可以清掉了（时间线会从 output 渲染）
    liveText.value = ''
  }
  return ok
}

async function runNextStep() {
  const idx = currentRun.value?.next_runnable_idx
  if (!idx) return
  await executeStep(idx)
}

/** 连续执行，直到需要人工确认 / 出错 / 全部完成。 */
async function runAll() {
  if (autoRunning.value || executing.value) return
  autoRunning.value = true
  stopRequested = false
  try {
    // 上限只是防止意外死循环；正常情况靠 next_runnable_idx 变 null 收敛
    for (let guard = 0; guard < 50; guard += 1) {
      if (stopRequested) break
      const idx = currentRun.value?.next_runnable_idx
      if (!idx) break
      const ok = await executeStep(idx)
      // 失败或需要审批 → 停下，把决定权交回用户
      if (!ok || stopRequested) break
    }
  } finally {
    autoRunning.value = false
    stopRequested = false
  }
}

function stopRun() {
  stopRequested = true
  if (execAbort) execAbort.abort()
}

async function onApproveStep(idx) {
  await guard(async () => {
    const res = await workflowApi.approveStep(currentRun.value.id, idx)
    currentRun.value = res.run
    await loadRuns()
  })
}

async function onRetryStep(idx) {
  await guard(async () => {
    const res = await workflowApi.retryStep(currentRun.value.id, idx)
    currentRun.value = res.run
    await loadRuns()
  })
}

async function onSkipStep(idx) {
  await guard(async () => {
    const res = await workflowApi.skipStep(currentRun.value.id, idx)
    currentRun.value = res.run
    await loadRuns()
  })
}

/**
 * 驳回某一步 → 该步及之后重新规划（前序产出保留）。
 *
 * 服务端把状态置回 `planning`，所以这里紧接着就要重新生成任务书 ——
 * 否则用户会停在「待规划」却不知道下一步该点哪。
 */
async function onRejectStep(idx, comment) {
  await guard(async () => {
    const res = await workflowApi.rejectStep(currentRun.value.id, idx, comment)
    currentRun.value = res.run
    await loadRuns()
    await generatePlan()
    // ⚠️ 必须在 generatePlan 之后再设：它内部会 resetPlanState() 清空提示
    if (res.preserved) {
      planError.value = ''
      planNotice.value = `已保留前 ${res.preserved} 步的产出，已从第 ${res.rejected_from} 步重新规划`
    }
  })
}

async function onCancelRun() {
  await guard(async () => {
    const res = await workflowApi.cancelRun(currentRun.value.id, '')
    currentRun.value = res.run
    await loadRuns()
  })
}

/** 执行中切换模式（设计 §7.3）：切换后立即对尚未开始的步骤生效。 */
async function onSetMode(mode) {
  if (!currentRun.value || currentRun.value.mode === mode) return
  await guard(async () => {
    currentRun.value = await workflowApi.updateRun(currentRun.value.id, { mode })
  })
}

// ── 交付物与分享 ──────────────────────────────────────────────────
async function loadArtifacts() {
  if (!currentRunId.value) {
    artifacts.value = []
    return
  }
  try {
    const res = await workflowApi.listArtifacts(currentRunId.value)
    artifacts.value = res.artifacts || []
  } catch (e) {
    artifacts.value = []
  }
}

async function onDeliver() {
  await guard(async () => {
    delivering.value = true
    try {
      const res = await workflowApi.deliverRun(currentRun.value.id)
      currentRun.value = res.run
      await loadArtifacts()
    } finally {
      delivering.value = false
    }
  })
}

async function downloadArtifact(item) {
  await guard(async () => {
    await downloadRunFile(item.id, item.filename)
  })
}

/**
 * 从步骤卡片里直接下载该步产出的文件。
 *
 * 步骤卡片只带 `{id, filename}`，这里从产物列表里补齐信息；
 * 列表里没有（例如刚产出、列表还没刷新）就用文件名兜底 ——
 * 有名字就够下载了，不该因为"列表还没刷"让按钮点不动。
 */
async function downloadArtifactById(fileId) {
  if (!fileId) return
  const hit = artifacts.value.find((item) => item.id === fileId)
  await guard(async () => {
    await downloadRunFile(fileId, hit?.filename || 'download')
  })
}

async function onShareRun() {
  await guard(async () => {
    const res = await workflowApi.shareRun(currentRun.value.id)
    shareUrl.value = `${window.location.origin}${res.url_path}`
  })
}

async function copyShareUrl() {
  try {
    await navigator.clipboard.writeText(shareUrl.value)
  } catch (e) {
    // 剪贴板权限被拒时静默失败：输入框本身可选中复制
    console.warn('复制失败:', e)
  }
}

function formatSize(bytes) {
  const value = Number(bytes) || 0
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

// ── 统一错误处理 ──────────────────────────────────────────────────
async function guard(fn) {
  busy.value = true
  errorMessage.value = ''
  try {
    await fn()
  } catch (e) {
    errorMessage.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  localStorage.removeItem('chatData')
  router.push('/login')
}
</script>

<style scoped>
.work-header {
  border-bottom: 1px solid var(--color-border);
}

.work-body {
  background-color: var(--color-bg);
}

.error-text {
  color: var(--state-error);
  max-width: 46ch;
  text-align: right;
}

.status-badge,
.status-dot {
  color: var(--color-text-secondary);
}

.status-badge {
  padding: 2px 8px;
  font-size: var(--text-xs);
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  border: 1px solid var(--color-border-light);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: var(--color-text-tertiary);
}

.status-dot.is-done,
.status-badge.is-done {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}
.status-dot.is-done {
  background-color: var(--state-success);
}
.status-dot.is-awaiting_approval,
.status-badge.is-awaiting_approval {
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}
.status-dot.is-awaiting_approval {
  background-color: var(--state-warning);
}
.status-dot.is-running,
.status-badge.is-running {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}
.status-dot.is-running {
  background-color: var(--state-info);
}
.status-dot.is-approved,
.status-badge.is-approved {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}
.status-dot.is-approved {
  background-color: var(--state-success);
}
.status-dot.is-failed,
.status-badge.is-failed {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}
.status-dot.is-failed {
  background-color: var(--state-error);
}

.task-hint,
.task-meta {
  color: var(--color-text-tertiary);
}

.task-del {
  color: var(--color-text-tertiary);
  padding: 2px;
}

/* ===== 空态与路线图 ===== */
.work-empty-icon {
  border-radius: var(--radius-lg);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.work-empty-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}

.work-empty-desc {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  max-width: 460px;
}

/* ===== 引用文件：缺失步骤提示与注入量说明 ===== */
.usage-line {
  margin-top: 10px;
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
  text-align: right;
}

.folder-callout {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--state-warning-bg);
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
}

.folder-callout-text {
  font-size: var(--text-sm);
  color: var(--state-warning);
  line-height: 1.7;
}

.folder-callout-meta {
  margin-top: 3px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.folder-callout-actions {
  display: flex;
  gap: 8px;
  margin-top: 9px;
  flex-wrap: wrap;
}

.file-context-note {
  margin-top: 8px;
  padding: 6px 9px;
  font-size: var(--text-xs);
  line-height: 1.7;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
}

.file-inject {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--state-warning);
}

.import-error {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding: 7px 9px;
  font-size: var(--text-xs);
  line-height: 1.7;
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

/* ===== 任务详情卡片 ===== */
.run-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.card {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  padding: 14px 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
  margin-bottom: 8px;
}

.card-head .card-title {
  margin-bottom: 0;
}

.card-text {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.card-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.7;
}

.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.thinking-text {
  font-family: var(--font-body);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}

/* 生成中的光标：让「还在写」这件事一眼可见 */
.caret {
  display: inline-block;
  width: 7px;
  height: 14px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background-color: var(--color-primary);
}

.plan-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 2px;
}

.plan-toolbar .card-hint {
  margin: 0;
}

.clarify-summary {
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.9;
}

.field-error {
  font-size: var(--text-xs);
  color: var(--state-error);
  line-height: 1.7;
}

.plan-notice {
  font-size: var(--text-xs);
  color: var(--state-info);
  line-height: 1.7;
}

/* ===== 交付物与分享 ===== */
.share-box {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.share-box .form-input {
  flex: 1;
  min-width: 0;
  font-size: var(--text-xs);
  font-family: var(--font-mono);
}

.artifact-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.artifact-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.artifact-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  color: var(--color-primary);
  text-align: left;
  min-width: 0;
  word-break: break-all;
}

.artifact-link:hover {
  text-decoration: underline;
}

.artifact-meta {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.file-row {
  padding: 7px 0;
  border-top: 1px solid var(--color-border-light);
  font-size: var(--text-sm);
}

.file-row:first-of-type {
  border-top: none;
}

.file-icon {
  color: var(--color-text-tertiary);
}

.file-name {
  color: var(--color-text-primary);
}

.file-size {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.file-warn {
  font-size: var(--text-xs);
  color: var(--state-warning);
  flex-shrink: 0;
}

.mini-icon-btn {
  padding: 3px;
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
}

.mini-icon-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mini-icon-btn.danger:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.mini-btn {
  padding: 3px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.mini-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mini-btn[disabled] {
  opacity: 0.5;
  cursor: default;
}

.hidden-input {
  display: none;
}

/* ===== 输入区 ===== */
.pending-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.pending-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
  max-width: 240px;
}

.pending-chip button {
  color: var(--color-text-tertiary);
  font-size: 14px;
  line-height: 1;
}

.composer-box {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  padding: 10px 12px 8px;
}

.composer-textarea {
  width: 100%;
  border: none;
  resize: none;
  background: transparent;
  color: var(--color-text-primary);
  font-size: var(--text-sm);
  line-height: 1.6;
  max-height: 200px;
}

.composer-textarea::placeholder {
  color: var(--color-text-tertiary);
}

.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.composer-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.composer-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.composer-icon-btn:hover {
  background-color: var(--color-surface-secondary);
}

.composer-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
}

.composer-chip:hover,
.composer-chip.on {
  background-color: var(--color-primary-lighter);
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.composer-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-full);
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.composer-send[disabled] {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  cursor: default;
}

.composer-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 2px 0;
}

.composer-meta-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.composer-meta-btn:hover,
.composer-meta-btn.on {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

/* ===== 下拉菜单 ===== */
.menu-anchor {
  position: relative;
}

.menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  z-index: 30;
  min-width: 180px;
  max-height: 320px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-elevated);
}

.menu-wide {
  min-width: 280px;
}

/* 本地文件夹区 */
.menu-folder {
  padding: 2px 0 4px;
}

.menu-folder-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 9px 6px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.menu-folder-path {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
}

.menu-folder-empty {
  font-size: 11px;
  opacity: 0.75;
}

.menu-up {
  bottom: calc(100% + 8px);
}

.menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  text-align: left;
}

.menu-item:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.menu-item.active {
  color: var(--color-primary);
  background-color: var(--color-primary-lighter);
}

.menu-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-item-tag {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

/* 自定义模型用主色标出来，与内置模型一眼可分 */
.menu-item-tag.custom {
  color: var(--color-primary);
}

.menu-manage {
  width: 100%;
  margin-top: 4px;
  padding: 7px;
  font-size: var(--text-xs);
  text-align: center;
  color: var(--color-text-secondary);
  border-top: 1px solid var(--color-border-light);
}

.menu-manage:hover {
  color: var(--color-primary);
}

.menu-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
}

.menu-input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
}

.menu-divider {
  height: 1px;
  margin: 5px 2px;
  background-color: var(--color-border-light);
}

.menu-empty {
  padding: 8px 9px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.menu-note {
  padding: 6px 9px 2px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

/* ===== 侧边栏（scoped 下需各自声明）===== */
.sidebar {
  background-color: var(--color-surface);
  border-right: 1px solid var(--color-border);
}

.sidebar-header {
  padding: 20px;
}

.sidebar-title {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  color: var(--color-text-primary);
  margin-bottom: 16px;
}

.new-chat-btn {
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
  background-color: transparent;
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.new-chat-btn:hover {
  background-color: var(--color-primary-lighter);
}

.sidebar-footer {
  border-top: 1px solid var(--color-border);
}

.sidebar-nav-btn {
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.sidebar-nav-btn:hover {
  background-color: var(--color-surface-secondary);
}

.sidebar-nav-btn.logout:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.chat-list::-webkit-scrollbar {
  width: 4px;
}

.chat-item {
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
  color: var(--color-text-primary);
}

.chat-item:hover {
  background-color: var(--color-surface-secondary);
}

.chat-item.active {
  background-color: var(--color-primary-lighter);
}

.stats-text {
  color: var(--color-text-tertiary);
}

.chat-header-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}
</style>
