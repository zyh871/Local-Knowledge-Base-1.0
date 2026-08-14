<template>
  <div class="chat-main flex h-screen overflow-hidden">
    <!-- Sidebar -->
    <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col">
      <div class="sidebar-header p-5">
        <h1 class="sidebar-title mb-4 font-semibold">知识库问答</h1>
        <button @click="newChat" class="new-chat-btn flex items-center justify-center gap-2 w-full py-2 text-sm whitespace-nowrap">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          新对话
        </button>
      </div>

      <div class="chat-list flex-1 overflow-y-auto px-3">
        <!-- Search -->
        <div class="mb-3">
          <div class="search-box relative">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="absolute left-2.5 top-1/2 -translate-y-1/2">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              v-model="chatSearchQuery"
              placeholder="搜索对话..."
              class="w-full text-sm pl-8 pr-3 py-2 bg-transparent border border-current rounded-lg"
            />
          </div>
        </div>
        
        <!-- Select all -->
        <div v-if="chatHistory.length > 0" class="flex items-center justify-between px-1 mb-2">
          <template v-if="batchMode">
            <label class="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" :checked="selectedChatIds.size === chatHistory.length && chatHistory.length > 0" @change="toggleSelectAll" />
              全选
            </label>
            <div class="flex items-center gap-2">
              <button @click="batchDeleteChats" class="text-xs text-red-500">
                删除选中 ({{ selectedChatIds.size }})
              </button>
              <button @click="exitBatchMode" class="text-xs text-gray-500">取消</button>
            </div>
          </template>
          <template v-else>
            <span class="text-xs text-gray-400">&nbsp;</span>
            <button @click="enterBatchMode" class="text-xs text-gray-500 hover:text-gray-300">批量管理</button>
          </template>
        </div>

        <!-- 最近对话标题 -->
        <div v-if="chatHistory.length > 0" class="chat-section-header text-xs text-gray-400 px-3 py-2 font-medium tracking-wide">最近对话</div>

        <div
          v-for="chat in sortedChatHistory"
          :key="chat.id"
          :class="['chat-item group flex items-center gap-2 px-3 py-2.5 cursor-pointer mb-1', { active: chat.id === conversationId, selected: batchMode && selectedChatIds.has(chat.id), pinned: isPinned(chat.id) }]"
          @click="batchMode ? toggleSelectChat(chat.id) : switchChat(chat.id)"
        >
          <input v-if="batchMode" type="checkbox" :checked="selectedChatIds.has(chat.id)" @click.stop="toggleSelectChat(chat.id)" class="mt-0.5 shrink-0" />
          <svg v-if="isPinned(chat.id)" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0 chat-item-icon pinned-icon">
            <path d="M12 17v5"/>
            <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0 chat-item-icon">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <div class="flex-1 min-w-0">
            <template v-if="editingChatId === chat.id">
              <input
                v-model="editingChatTitle"
                @click.stop
                @keydown.enter="saveRename"
                @keydown.esc="cancelRename"
                @blur="saveRename"
                class="rename-input w-full text-sm px-1.5 py-0.5 outline-none"
                ref="renameInputRef"
              />
            </template>
            <template v-else>
              <div class="relative">
                <p class="text-sm truncate chat-item-title">{{ chat.title }}</p>
                <div class="chat-tooltip">{{ chat.title }}</div>
              </div>
            </template>
          </div>
          <div v-if="editingChatId !== chat.id && !batchMode" class="flex items-center shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
            <button @click.stop="openContextMenuAtButton($event, chat)" class="p-1 chat-menu-btn" aria-label="更多操作" title="更多操作">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="5" r="2"/>
                <circle cx="12" cy="12" r="2"/>
                <circle cx="12" cy="19" r="2"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Context Menu -->
        <teleport to="body">
          <div
            v-if="contextMenu.visible"
            class="context-menu"
            :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
            @click.stop
          >
            <div class="context-menu-item" @click="handleContextAction('rename')">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 20h9"/>
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
              </svg>
              <span>重命名</span>
            </div>
            <div class="context-menu-item" @click="handleContextAction('pin')">
              <svg v-if="!isPinned(contextMenu.chatId)" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 17v5"/>
                <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
              </svg>
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 17v5"/>
                <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
                <line x1="2" y1="2" x2="22" y2="22"/>
              </svg>
              <span>{{ isPinned(contextMenu.chatId) ? '取消置顶' : '置顶此对话' }}</span>
            </div>
            <div class="context-menu-item" @click="handleContextAction('share')">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
                <polyline points="16 6 12 2 8 6"/>
                <line x1="12" y1="2" x2="12" y2="15"/>
              </svg>
              <span>分享此对话</span>
            </div>
            <div class="context-menu-item has-submenu" @click="handleContextAction('move')">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              </svg>
              <span>移动到分组</span>
              <svg class="submenu-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </div>
            <div v-if="contextMenu.showGroupSubmenu" class="context-submenu" :style="{ top: contextMenu.y + 'px', left: (contextMenu.x + 200) + 'px' }">
              <div v-if="groups.length === 0" class="context-menu-item disabled">
                <span>暂无分组</span>
              </div>
              <div
                v-for="g in groups"
                :key="g"
                class="context-menu-item"
                @click="handleContextAction('move:' + g)"
              >
                <span>{{ g }}</span>
              </div>
              <div class="context-menu-item" @click="handleContextAction('move:__new__')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 5v14M5 12h14"/>
                </svg>
                <span>新建分组...</span>
              </div>
            </div>
            <div class="context-menu-item has-submenu" @click="handleContextAction('export')">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              <span>导出对话</span>
              <svg class="submenu-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </div>
            <div v-if="contextMenu.showExportSubmenu" class="context-submenu" :style="{ top: contextMenu.y + 'px', left: (contextMenu.x + 200) + 'px' }">
              <div class="context-menu-item" @click="handleContextAction('export_md')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span>Markdown 格式</span>
              </div>
              <div class="context-menu-item" @click="handleContextAction('export_image')">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                  <circle cx="8.5" cy="8.5" r="1.5"/>
                  <polyline points="21 15 16 10 5 21"/>
                </svg>
                <span>图片长图</span>
              </div>
            </div>
            <div class="context-menu-divider"></div>
            <div class="context-menu-item danger" @click="handleContextAction('delete')">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
              <span>删除此对话</span>
            </div>
          </div>
        </teleport>
      </div>

      <div class="sidebar-footer px-3 py-4">
        <nav class="flex flex-col gap-1">
          <button @click="toggleDocumentPanel" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            文档管理
          </button>
          <button @click="showProfilePanel = true" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
            用户画像
          </button>
          <button @click="showLongTextPanel = true" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            长文本工具
          </button>
          <button @click="toggleTheme" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg v-if="theme === 'light'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <circle cx="12" cy="12" r="5"/>
              <line x1="12" y1="1" x2="12" y2="3"/>
              <line x1="12" y1="21" x2="12" y2="23"/>
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
              <line x1="1" y1="12" x2="3" y2="12"/>
              <line x1="21" y1="12" x2="23" y2="12"/>
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
            </svg>
            {{ theme === 'light' ? '深色模式' : '浅色模式' }}
          </button>
          <button @click="logout" class="sidebar-nav-btn logout flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            退出登录
          </button>
        </nav>
      </div>
    </aside>

    <!-- Mobile sidebar drawer -->
    <div v-if="sidebarOpen" class="mobile-sidebar-overlay lg:hidden" @click="sidebarOpen = false"></div>
    <div v-if="sidebarOpen" class="mobile-sidebar-drawer lg:hidden">
      <div class="sidebar-header p-5 flex items-center justify-between">
        <h1 class="sidebar-title mb-0 font-semibold">知识库问答</h1>
        <button @click="sidebarOpen = false" class="p-1.5 header-menu-btn" aria-label="关闭侧边栏">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <div class="sidebar-content flex-1 overflow-y-auto" @click.stop>
        <button @click="newChat; sidebarOpen = false" class="new-chat-btn flex items-center justify-center gap-2 w-full mx-5 mb-4 py-2 text-sm whitespace-nowrap">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          新对话
        </button>

        <div class="chat-list flex-1 overflow-y-auto px-3">
          <div class="mb-3">
            <div class="search-box relative">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="absolute left-2.5 top-1/2 -translate-y-1/2">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
              <input
                v-model="chatSearchQuery"
                placeholder="搜索对话..."
                class="w-full text-sm pl-8 pr-3 py-2 bg-transparent border border-current rounded-lg"
              />
            </div>
          </div>

          <div v-if="chatHistory.length > 0" class="chat-section-header text-xs text-gray-400 px-3 py-2 font-medium tracking-wide">最近对话</div>

          <div
            v-for="chat in sortedChatHistory"
            :key="'mobile-' + chat.id"
            :class="['chat-item group flex items-center gap-2 px-3 py-2.5 cursor-pointer mb-1', { active: chat.id === conversationId }]"
            @click="switchChat(chat.id); sidebarOpen = false"
          >
            <svg v-if="isPinned(chat.id)" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0 chat-item-icon pinned-icon">
              <path d="M12 17v5"/>
              <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0 chat-item-icon">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <div class="flex-1 min-w-0">
              <p class="text-sm truncate chat-item-title">{{ chat.title }}</p>
            </div>
            <div class="flex items-center shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
              <button @click.stop="openContextMenuAtButton($event, chat)" class="p-1 chat-menu-btn" aria-label="更多操作">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="5" r="2"/>
                  <circle cx="12" cy="12" r="2"/>
                  <circle cx="12" cy="19" r="2"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="sidebar-footer px-3 py-4">
        <nav class="flex flex-col gap-1">
          <button @click="toggleDocumentPanel; sidebarOpen = false" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            文档管理
          </button>
          <button @click="showProfilePanel = true; sidebarOpen = false" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
            用户画像
          </button>
          <button @click="showLongTextPanel = true; sidebarOpen = false" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            长文本工具
          </button>
          <button @click="toggleTheme" class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg v-if="theme === 'light'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <circle cx="12" cy="12" r="5"/>
              <line x1="12" y1="1" x2="12" y2="3"/>
              <line x1="12" y1="21" x2="12" y2="23"/>
            </svg>
            {{ theme === 'light' ? '深色模式' : '浅色模式' }}
          </button>
          <button @click="logout" class="sidebar-nav-btn logout flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="nav-icon">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            退出登录
          </button>
        </nav>
      </div>
    </div>

    <!-- Main chat area -->
    <section class="chat-section flex-1 flex flex-col min-w-0">
      <!-- Backend connection warning -->
      <div v-if="backendOffline" class="backend-offline-banner flex items-center justify-center gap-2 px-4 py-2 text-xs shrink-0">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        后端服务未连接，请启动后端服务（python -m uvicorn app.main:app --port 8000）
      </div>

      <!-- Chat header -->
      <header class="chat-header flex items-center justify-between px-4 py-4 lg:px-6 shrink-0">
        <div class="flex items-center gap-3 min-w-0">
          <button class="lg:hidden p-1.5 shrink-0 header-menu-btn" @click="sidebarOpen = !sidebarOpen" aria-label="打开侧边栏">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="3" y1="12" x2="21" y2="12"/>
              <line x1="3" y1="6" x2="21" y2="6"/>
              <line x1="3" y1="18" x2="21" y2="18"/>
            </svg>
          </button>
          <h2 class="truncate font-semibold chat-header-title">{{ currentChatTitle }}</h2>
        </div>
        <div class="flex items-center gap-4 shrink-0">
          <span v-if="stats" class="hidden sm:inline text-xs whitespace-nowrap stats-text">
            {{ stats.vector_count }} 向量 · {{ stats.chroma_exists ? '就绪' : '未创建' }}
          </span>
          <button @click="refreshStats" class="text-sm whitespace-nowrap refresh-btn">
            刷新状态
          </button>
        </div>
      </header>

      <!-- Messages area -->
      <div class="messages-area flex-1 overflow-y-auto px-4 py-6 lg:px-6" ref="chatBox">
        <!-- Empty state -->
        <div v-if="messages.length === 0" class="empty-state flex-col items-center justify-center h-full">
          <div class="empty-icon w-20 h-20 flex items-center justify-center mb-6">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <h3 class="mb-2 font-semibold empty-title">欢迎使用知识库问答系统</h3>
          <p class="text-sm empty-desc">上传文档或直接提出您的问题，我会基于知识库为您解答</p>

          <!-- Quick action cards -->
          <div class="quick-action-cards mt-8 w-full max-w-md px-4">
            <p class="text-xs text-center mb-4 action-cards-title">快捷操作</p>
            <div class="grid grid-cols-3 gap-3">
              <button @click="toggleDocumentPanel" class="quick-action-card flex flex-col items-center gap-2 p-4">
                <div class="action-card-icon w-10 h-10 flex items-center justify-center">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="17 8 12 3 7 8"/>
                    <line x1="12" y1="3" x2="12" y2="15"/>
                  </svg>
                </div>
                <span class="text-xs">上传文档</span>
              </button>
              <button @click="showDocumentPanel = true" class="quick-action-card flex flex-col items-center gap-2 p-4">
                <div class="action-card-icon w-10 h-10 flex items-center justify-center">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                  </svg>
                </div>
                <span class="text-xs">文档管理</span>
              </button>
              <button @click="showLongTextPanel = true" class="quick-action-card flex flex-col items-center gap-2 p-4">
                <div class="action-card-icon w-10 h-10 flex items-center justify-center">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                  </svg>
                </div>
                <span class="text-xs">长文本工具</span>
              </button>
            </div>
          </div>

          <!-- Example questions -->
          <div class="mt-8 w-full max-w-lg px-4 example-questions">
            <p class="text-xs text-center mb-4 example-title">试试这些问题：</p>
            <div class="flex flex-wrap justify-center gap-2">
              <button
                v-for="(q, idx) in localExampleQuestions"
                :key="idx"
                @click="askExampleQuestion(q)"
                class="example-question-btn text-xs px-4 py-2"
              >
                {{ q }}
              </button>
            </div>
          </div>
        </div>

        <!-- Messages -->
        <div v-for="(msg, idx) in messages" :key="idx" :class="['message-item', msg.role]">
          <!-- User message -->
          <template v-if="msg.role === 'user'">
            <div class="avatar w-8 h-8 flex items-center justify-center shrink-0">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
            </div>
            <div class="bubble max-w-[65%] px-4 py-3">
              <img v-if="msg.image" :src="msg.image" :alt="msg.image_name || '图片'" class="rounded-lg max-w-full max-h-60 mb-2 border" />
              <p class="text-sm message-text">{{ msg.content }}</p>
              <div class="flex items-center justify-end gap-2 mt-2">
                <span class="text-xs message-time">{{ formatTime() }}</span>
              </div>
            </div>
          </template>

          <!-- Assistant message -->
          <template v-else>
            <div class="avatar w-8 h-8 flex items-center justify-center shrink-0 assistant-avatar">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
              </svg>
            </div>
            <div class="bubble assistant-bubble max-w-[65%] px-4 py-3">
              <!-- thinking state -->
              <div v-if="msg.status === 'thinking'" class="thinking-indicator">
                <div class="typing-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
                <span class="typing-text">思考中...</span>
              </div>

              <!-- streaming / done state -->
              <template v-else>
                <div class="markdown-body text-sm message-text" v-html="renderMarkdown(msg.content)"></div>

                <!-- Source section: only shown when done and sources exist -->
                <div v-if="msg.status === 'done' && msg.sources && msg.sources.length > 0" class="source-section mt-3 pt-3">
                  <button @click="toggleSourceExpand(idx)" class="flex items-center gap-1.5 text-xs font-medium mb-2 source-toggle-btn">
                    <svg :class="['w-3.5 h-3.5 transition-transform', { 'rotate-180': expandedSources[idx] }]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="6 9 12 15 18 9"/>
                    </svg>
                    <span v-if="isWebSource(msg)">网络来源 ({{ msg.sources.length }})</span>
                    <span v-else>参考来源 ({{ msg.sources.length }})</span>
                  </button>
                  <div v-if="expandedSources[idx]" class="source-content p-3">
                    <!-- 网络来源：可点击的链接 -->
                    <div v-if="isWebSource(msg)">
                      <div v-for="(source, sIdx) in msg.sources" :key="sIdx" class="web-source-item mb-2 last:mb-0">
                        <a :href="source.href || source.url" target="_blank" rel="noopener noreferrer" class="web-source-title">
                          <span class="source-num">{{ sIdx + 1 }}</span>
                          <span class="source-title-text">{{ source.title || source.file }}</span>
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="external-link-icon">
                            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                            <polyline points="15 3 21 3 21 9"/>
                            <line x1="10" y1="14" x2="21" y2="3"/>
                          </svg>
                        </a>
                        <p v-if="source.snippet || source.content_preview" class="text-xs mt-1 source-preview-text">{{ source.snippet || source.content_preview }}</p>
                        <p v-if="source.source" class="text-xs mt-1 source-engine">{{ source.source }}</p>
                      </div>
                    </div>
                    <!-- 知识库来源 -->
                    <div v-else>
                      <div v-for="(source, sIdx) in msg.sources" :key="sIdx" class="source-item mb-2 last:mb-0">
                        <p class="text-sm truncate source-file">{{ source.file }}</p>
                        <p class="text-xs mt-1 line-clamp-2 source-preview">{{ source.content_preview }}...</p>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Meta + actions: only shown when done -->
                <div v-if="msg.status === 'done'" class="flex items-center gap-2 mt-3 message-meta">
                  <span class="inline-flex items-center px-2 py-0.5 text-xs whitespace-nowrap data-source-badge" :class="msg.data_source">
                    {{ getDataSourceLabel(msg.data_source) }}
                  </span>
                  <span v-if="msg.intent" class="inline-flex items-center px-2 py-0.5 text-xs whitespace-nowrap intent-badge" :class="`intent-${msg.intent}`">
                    {{ getIntentLabel(msg.intent) }}
                  </span>
                  <span class="text-xs message-time">{{ formatTime() }}</span>
                  <div class="flex items-center gap-1 ml-auto">
                    <button @click="submitFeedback(idx, 1)" :class="['msg-action-btn', { 'active': feedbackState[idx] === 1 }]" :title="feedbackState[idx] === 1 ? '已点赞' : '有用'" aria-label="有用">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M20 8L10 17l-5-5"/>
                      </svg>
                    </button>
                    <button @click="submitFeedback(idx, -1)" :class="['msg-action-btn', { 'active': feedbackState[idx] === -1 }]" :title="feedbackState[idx] === -1 ? '已踩' : '没用'" aria-label="没用">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M4 16l10-9 5 5"/>
                      </svg>
                    </button>
                    <button @click="speakAnswer(idx)" :class="['msg-action-btn', { active: speakingIdx === idx }]" :title="speakingIdx === idx ? '停止朗读' : '朗读'" aria-label="朗读">
                      <svg v-if="speakingIdx !== idx" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                        <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>
                      </svg>
                      <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="6" y="4" width="4" height="16"/>
                        <rect x="14" y="4" width="4" height="16"/>
                      </svg>
                    </button>
                    <button @click="copyAnswer(idx)" class="msg-action-btn" :title="copiedIdx === idx ? '已复制' : '复制'" aria-label="复制">
                      <svg v-if="copiedIdx !== idx" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                      </svg>
                      <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"/>
                      </svg>
                    </button>
                    <button @click="regenerateAnswer(idx)" class="msg-action-btn" title="重新生成" aria-label="重新生成">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="23 4 23 10 17 10"/>
                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                      </svg>
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>
      </div>

      <!-- Input area -->
      <div class="input-area shrink-0 px-4 pt-4 pb-5 lg:px-6">
        <!-- Quick tool pills - above input -->
        <div class="flex items-center gap-2 mb-3 quick-tools">
          <button class="quick-pill inline-flex items-center gap-1.5 px-3 py-1.5 text-xs whitespace-nowrap" @click="openLongText('summary')" :disabled="loading">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            摘要
          </button>
          <button class="quick-pill inline-flex items-center gap-1.5 px-3 py-1.5 text-xs whitespace-nowrap" @click="openLongText('translate')" :disabled="loading">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M5 8l6 6"/>
              <path d="M4 14l6-6 2-3"/>
              <path d="M2 5h12"/>
              <path d="M7 2h1"/>
              <path d="M22 20l-3-3-5 5"/>
              <path d="M16 17l-2 2"/>
            </svg>
            翻译
          </button>
          <button class="quick-pill inline-flex items-center gap-1.5 px-3 py-1.5 text-xs whitespace-nowrap" @click="openLongText('qa')" :disabled="loading">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
              <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            问答
          </button>
        </div>

        <!-- Input wrapper with embedded buttons -->
        <div class="input-wrapper flex items-center gap-2 px-3 py-2.5">
          <div class="flex items-center gap-1 shrink-0">
            <label class="toolbar-btn flex items-center justify-center p-2 cursor-pointer" title="上传文件">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              <input type="file" multiple accept=".txt,.pdf,.md,.docx,.pptx,.ppt,.xlsx,.xls,.csv,.jpg,.jpeg,.png,.bmp,.tiff" @change="handleFileUpload" class="hidden" />
            </label>
            <label class="toolbar-btn flex items-center justify-center p-2 cursor-pointer" :class="{ active: pendingImage }" title="图片问答">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
              <input type="file" accept="image/*" @change="handleImageSelect" class="hidden" :disabled="loading" />
            </label>
            <button :class="['toolbar-btn flex items-center justify-center p-2', { active: forceSearch }]" @click="forceSearch = !forceSearch" :title="forceSearch ? '已开启联网搜索' : '开启联网搜索'" aria-label="联网搜索">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </button>
          </div>
          <div class="flex-1 min-w-0">
            <div v-if="pendingImage" class="image-preview-bar flex items-center gap-2 mb-2 px-2 py-1.5 rounded-lg">
              <img :src="pendingImage.url" :alt="pendingImage.name" class="w-10 h-10 object-cover rounded border" />
              <div class="flex-1 min-w-0">
                <p class="text-xs truncate">{{ pendingImage.name }}</p>
                <p class="text-xs text-gray-400">{{ formatImageSize(pendingImage.size) }}</p>
              </div>
              <button @click="clearPendingImage" class="p-1 text-gray-400 hover:text-red-500" title="移除图片" aria-label="移除图片">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
              </button>
            </div>
            <textarea
              v-model="question"
              @keydown.enter.exact.prevent="sendQuestion"
              @paste="handleLongTextPaste"
              :placeholder="pendingImage ? '输入关于这张图片的问题...（Enter 发送）' : '输入您的问题...（Enter 发送，Shift+Enter 换行）'"
              :disabled="loading"
              rows="1"
              class="question-textarea w-full resize-none border-0 outline-none bg-transparent text-sm"
            ></textarea>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button
              v-if="!loading"
              @click="toggleVoiceInput"
              :class="['voice-btn flex items-center justify-center p-2', { listening: isListening }]"
              :title="isListening ? '停止识别' : (speechSupported ? '语音输入' : '当前浏览器不支持语音识别')"
              :disabled="!speechSupported"
              aria-label="语音输入"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
            </button>
            <button v-if="!loading" @click="sendQuestion" :disabled="!question.trim()" class="send-btn flex items-center justify-center p-2.5 shrink-0 bg-primary text-primary-contrast rounded-lg hover:bg-primary/90 transition-colors" aria-label="发送" title="发送">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
            <button v-else @click="stopGeneration" class="stop-btn flex items-center justify-center p-2.5 shrink-0 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors" aria-label="停止生成" title="停止生成">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="1.5"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- Long text paste prompt -->
    <div v-if="longTextPromptVisible" class="modal-overlay-wrap">
      <div class="modal-overlay" @click="longTextPromptVisible = false"></div>
      <div class="modal-content max-w-md">
        <div class="modal-header">
          <h3>检测到长文本</h3>
          <button @click="longTextPromptVisible = false" class="modal-close-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="modal-body">
          <p class="text-sm">检测到您粘贴了一段长文本，您想如何处理？</p>
          <div class="flex gap-3 mt-4">
            <button @click="handleLongTextPromptAction('summary')" class="flex-1 py-3 text-sm font-medium">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="inline mr-2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
              生成摘要
            </button>
            <button @click="handleLongTextPromptAction('translate')" class="flex-1 py-3 text-sm font-medium">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="inline mr-2">
                <path d="M5 8l6 6"/>
                <path d="M4 14l6-6 2-3"/>
                <path d="M2 5h12"/>
                <path d="M7 2h1"/>
                <path d="M22 20l-3-3-5 5"/>
                <path d="M16 17l-2 2"/>
              </svg>
              翻译文本
            </button>
          </div>
          <button @click="longTextPromptVisible = false" class="w-full mt-3 py-2 text-sm text-gray-500">
            取消
          </button>
        </div>
      </div>
    </div>

    <!-- Document modal -->
    <div v-if="showDocumentPanel" class="modal-overlay-wrap">
      <div class="modal-overlay" @click="showDocumentPanel = false"></div>
      <div class="modal-content">
        <div class="modal-header">
          <h3>文档管理</h3>
          <button @click="showDocumentPanel = false" class="modal-close-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="modal-body">
          <div v-if="documents.length === 0" class="empty-documents">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-doc-icon">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            <p>暂无文档，请上传文档后开始提问</p>
          </div>
          <div v-else class="document-grid">
            <div v-for="doc in documents" :key="doc.filename" class="document-card">
              <div class="doc-icon">{{ getFileIcon(doc.extension) }}</div>
              <div class="doc-info">
                <div class="doc-name">{{ doc.filename }}</div>
                <div class="doc-size">{{ formatSize(doc.size) }}</div>
              </div>
              <button @click="deleteDocument(doc.filename)" class="doc-delete-btn">删除</button>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <label class="upload-btn-large">
            <input type="file" multiple accept=".txt,.pdf,.md,.docx,.pptx,.ppt,.xlsx,.xls,.csv,.jpg,.jpeg,.png,.bmp,.tiff" @change="handleFileUpload" />
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            上传文档
          </label>
        </div>
      </div>
    </div>

    <!-- Profile panel -->
    <div v-if="showProfilePanel" class="modal-overlay-wrap">
      <div class="modal-overlay" @click="showProfilePanel = false"></div>
      <div class="modal-content profile-modal">
        <div class="modal-header">
          <h3>用户画像（长期记忆）</h3>
          <button @click="showProfilePanel = false" class="modal-close-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="profile-body">
          <label class="form-label">姓名 / 称呼</label>
          <input v-model="profileForm.display_name" class="form-input" placeholder="例如：张工 / 老师" />
          <label class="form-label">职业</label>
          <input v-model="profileForm.profession" class="form-input" placeholder="例如：后端工程师" />
          <label class="form-label">偏好语言</label>
          <select v-model="profileForm.language" class="form-input">
            <option value="auto">自动检测</option>
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
          <label class="form-label">已知事实（每行一条）</label>
          <textarea v-model="profileFormFactsText" class="form-input" rows="4" placeholder="例如：住在北京&#10;对后端性能调优感兴趣"></textarea>
          <label class="form-label">偏好（JSON）</label>
          <textarea v-model="profileFormPrefsText" class="form-input" rows="3" placeholder='{"likes":["Python","FastAPI"]}'></textarea>
          <div class="profile-actions">
            <button class="btn-primary" @click="saveProfile">保存</button>
            <button class="btn-secondary" @click="loadProfile">重新加载</button>
          </div>
          <p class="hint-text">系统会在每次对话后自动抽取稳定事实并入此画像，你也可以手动维护。</p>
        </div>
      </div>
    </div>

    <!-- Long text panel -->
    <div v-if="showLongTextPanel" class="modal-overlay-wrap">
      <div class="modal-overlay" @click="showLongTextPanel = false"></div>
      <div class="modal-content longtext-modal">
        <div class="modal-header">
          <h3>长文本工具（摘要 / 翻译 / 问答）</h3>
          <button @click="showLongTextPanel = false" class="modal-close-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="profile-body">
          <div class="longtext-tabs">
            <button :class="['tab-btn', {active: longtextOp==='summary'}]" @click="longtextOp='summary'">摘要</button>
            <button :class="['tab-btn', {active: longtextOp==='translate'}]" @click="longtextOp='translate'">翻译</button>
            <button :class="['tab-btn', {active: longtextOp==='qa'}]" @click="longtextOp='qa'">问答</button>
          </div>
          <label class="form-label">粘贴长文本（支持数万字）</label>
          <textarea v-model="longtextInput" class="form-input" rows="10" placeholder="在此粘贴大段内容..."></textarea>
          <label v-if="longtextOp==='translate'" class="form-label">目标语言</label>
          <input v-if="longtextOp==='translate'" v-model="longtextTargetLang" class="form-input" placeholder="English / 日本語 / 한국어 ..." />
          <label v-if="longtextOp==='qa'" class="form-label">针对该文本的问题</label>
          <input v-if="longtextOp==='qa'" v-model="longtextQuestion" class="form-input" placeholder="例如：本文的核心结论是什么？" />
          <div class="profile-actions">
            <button class="btn-primary" :disabled="longtextLoading || !longtextInput.trim()" @click="runLongText">
              <span v-if="!longtextLoading">执行</span>
              <span v-else class="btn-spinner"></span>
            </button>
          </div>
          <div v-if="longtextResult" class="longtext-result">
            <pre>{{ longtextResult }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Share dialog -->
    <div v-if="shareDialogVisible" class="modal-overlay-wrap">
      <div class="modal-overlay" @click="closeShareDialog"></div>
      <div class="modal-content" style="max-width: 520px;">
        <div class="modal-header">
          <h3>分享对话</h3>
          <button @click="closeShareDialog" class="modal-close-btn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="modal-body">
          <p class="text-sm" style="margin-bottom: 12px; color: var(--color-text-secondary);">
            任何人通过以下链接都能只读查看此对话，链接将在 {{ shareTtlHours }} 小时后过期。
          </p>
          <div v-if="shareLoading" style="text-align:center;padding:20px;">生成中...</div>
          <div v-else-if="shareUrl" class="share-url-box">
            <input :value="shareUrl" readonly ref="shareUrlInput" class="form-input" @click="$event.target.select()" />
            <div style="display:flex;gap:8px;margin-top:12px;">
              <button class="btn-primary" @click="copyShareUrl">复制链接</button>
              <button class="btn-secondary" @click="closeShareDialog">关闭</button>
            </div>
          </div>
          <div v-else-if="shareError" class="state-error">{{ shareError }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, computed } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

const router = useRouter()
const API_BASE = '/api'
const token = ref(localStorage.getItem('token') || '')
const question = ref('')
const messages = ref([])
const loading = ref(false)
const chatBox = ref(null)
const showDocumentPanel = ref(false)
const showProfilePanel = ref(false)
const showLongTextPanel = ref(false)
const documents = ref([])
const stats = ref(null)
const conversationId = ref('')
const forceSearch = ref(false)
const pendingImage = ref(null)
const expandedSources = ref({})
const theme = ref(localStorage.getItem('theme') || 'light')
const copiedIdx = ref(-1)
const editingChatId = ref(null)
const editingChatTitle = ref('')
let abortController = null

const chatHistory = ref([])
const chatMessages = ref({})
const isTempChat = ref(false)

const contextMenu = reactive({ visible: false, x: 0, y: 0, chatId: '', showGroupSubmenu: false, showExportSubmenu: false })
const pinnedChats = ref([])
const groups = ref([])

const sortedChatHistory = computed(() => {
  const pinnedIds = new Set(pinnedChats.value)
  const pinned = chatHistory.value.filter(c => pinnedIds.has(c.id))
  const unpinned = chatHistory.value.filter(c => !pinnedIds.has(c.id))
  return [...pinned, ...unpinned]
})

const profileForm = ref({ display_name: '', profession: '', language: 'auto' })
const profileFormFactsText = ref('')
const profileFormPrefsText = ref('')

const longtextOp = ref('summary')
const longtextInput = ref('')
const longtextTargetLang = ref('English')
const longtextQuestion = ref('')
const longtextLoading = ref(false)
const longtextResult = ref('')
const backendOffline = ref(false)
let backendCheckTimer = null
const sidebarOpen = ref(false)

const exampleQuestions = ref([])
const localExampleQuestions = ref([
  '介绍一下项目的主要功能',
  '支持哪些文件格式',
  '如何上传文档到知识库',
  '什么是 RAG 技术',
])
const feedbackState = ref({})
const chatSearchQuery = ref('')
const selectedChatIds = ref(new Set())
const batchMode = ref(false)
const longTextPromptVisible = ref(false)
const longTextPromptText = ref('')

// ===== 语音朗读 =====
const speakingIdx = ref(-1)
function speakAnswer(idx) {
  const msg = messages.value[idx]
  if (!msg || !msg.content) return
  if (speakingIdx.value === idx) {
    stopSpeak()
    return
  }
  stopSpeak()
  const synth = window.speechSynthesis
  if (!synth) {
    alert('当前浏览器不支持语音合成')
    return
  }
  const plain = msg.content
    .replace(/```[\s\S]*?```/g, ' 代码块已省略 ')
    .replace(/[*_`#>~]/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!plain) return
  const u = new SpeechSynthesisUtterance(plain)
  u.lang = msg.detected_language === 'en' ? 'en-US' : 'zh-CN'
  u.rate = 1.0
  u.onend = () => {
    if (speakingIdx.value === idx) speakingIdx.value = -1
  }
  u.onerror = () => {
    speakingIdx.value = -1
  }
  speakingIdx.value = idx
  synth.speak(u)
}
function stopSpeak() {
  if (window.speechSynthesis) window.speechSynthesis.cancel()
  speakingIdx.value = -1
}

// ===== 语音输入 =====
const speechSupported = ref(
  typeof window !== 'undefined' &&
  !!(window.SpeechRecognition || window.webkitSpeechRecognition)
)
const isListening = ref(false)
let recognition = null

function toggleVoiceInput() {
  if (!speechSupported.value) {
    alert('当前浏览器不支持 Web Speech 识别 API，请使用 Chrome / Edge 等浏览器')
    return
  }
  if (isListening.value) {
    stopVoiceInput()
    return
  }
  startVoiceInput()
}

function startVoiceInput() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  recognition = new SR()
  recognition.lang = 'zh-CN'
  recognition.interimResults = true
  recognition.continuous = false
  let finalText = ''
  recognition.onresult = (e) => {
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript
      if (e.results[i].isFinal) finalText += t
      else interim += t
    }
    question.value = (finalText + interim).trim()
  }
  recognition.onend = () => {
    isListening.value = false
  }
  recognition.onerror = (e) => {
    isListening.value = false
    if (e.error && e.error !== 'aborted' && e.error !== 'no-speech') {
      console.warn('语音识别错误:', e.error)
    }
  }
  try {
    recognition.start()
    isListening.value = true
  } catch (e) {
    isListening.value = false
  }
}

function stopVoiceInput() {
  try {
    recognition && recognition.stop()
  } catch (e) {}
  isListening.value = false
}

// ===== 分享链接 =====
const shareDialogVisible = ref(false)
const shareLoading = ref(false)
const shareUrl = ref('')
const shareError = ref('')
const shareTtlHours = ref(24)

async function shareChat(chatId) {
  shareDialogVisible.value = true
  shareLoading.value = true
  shareUrl.value = ''
  shareError.value = ''
  try {
    const msgs = chatMessages.value[chatId] || []
    const res = await fetch(`${API_BASE}/conversations/${encodeURIComponent(chatId)}/share`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ ttl_hours: shareTtlHours.value, messages: msgs })
    })
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      shareError.value = d.detail || '生成分享链接失败'
      return
    }
    const data = await res.json()
    const origin = window.location.origin
    shareUrl.value = `${origin}${data.url_path}`
  } catch (e) {
    shareError.value = '网络错误：' + e.message
  } finally {
    shareLoading.value = false
  }
}

async function copyShareUrl() {
  if (!shareUrl.value) return
  try {
    await navigator.clipboard.writeText(shareUrl.value)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = shareUrl.value
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  alert('分享链接已复制')
}

function closeShareDialog() {
  shareDialogVisible.value = false
  shareUrl.value = ''
  shareError.value = ''
}

// ===== 长图导出 =====
function exportChatImage(chatId) {
  const msgs = chatMessages.value[chatId] || []
  const chat = chatHistory.value.find((c) => c.id === chatId)
  const title = chat ? chat.title : '对话'
  if (!msgs.length) {
    alert('当前对话为空')
    return
  }
  renderChatToImage(msgs, title)
}

function wrapText(ctx, text, maxWidth) {
  const lines = []
  for (const para of text.split('\n')) {
    if (!para) {
      lines.push('')
      continue
    }
    let line = ''
    for (const ch of para) {
      const test = line + ch
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line)
        line = ch
      } else {
        line = test
      }
    }
    if (line) lines.push(line)
  }
  return lines
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function renderChatToImage(msgs, title) {
  const w = 760
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const isDark = document.documentElement.classList.contains('dark')
  const fontFamily =
    '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'
  const measure = document.createElement('canvas').getContext('2d')
  measure.font = `14px ${fontFamily}`
  const bubbleW = w * 0.7
  let totalH = 24 + 40 + 8 + 16 + 16
  const sizes = []
  for (const m of msgs) {
    const text = (m.content || '').replace(/```[\s\S]*?```/g, '[代码]').trim()
    if (!text) {
      sizes.push(0)
      continue
    }
    const lines = wrapText(measure, text, bubbleW - 24)
    const h = lines.length * 22 + 24 + 12
    sizes.push(h)
    totalH += h
  }
  totalH += 40

  const canvas = document.createElement('canvas')
  canvas.width = w * dpr
  canvas.height = totalH * dpr
  const ctx = canvas.getContext('2d')
  ctx.scale(dpr, dpr)
  ctx.font = `14px ${fontFamily}`
  ctx.textBaseline = 'alphabetic'

  ctx.fillStyle = isDark ? '#1f1f23' : '#ffffff'
  ctx.fillRect(0, 0, w, totalH)

  let y = 24
  ctx.fillStyle = isDark ? '#fafafa' : '#18181b'
  ctx.font = `bold 20px ${fontFamily}`
  ctx.fillText(title, 24, y + 20)
  y += 40
  ctx.font = `12px ${fontFamily}`
  ctx.fillStyle = isDark ? '#a1a1aa' : '#71717a'
  ctx.fillText(
    `导出时间: ${new Date().toLocaleString('zh-CN')}`,
    24,
    y + 12
  )
  y += 24
  ctx.strokeStyle = isDark ? '#3f3f46' : '#e4e4e7'
  ctx.beginPath()
  ctx.moveTo(24, y)
  ctx.lineTo(w - 24, y)
  ctx.stroke()
  y += 16

  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (!sizes[i]) continue
    const isUser = m.role === 'user'
    const text = (m.content || '').replace(/```[\s\S]*?```/g, '[代码]').trim()
    const lines = wrapText(ctx, text, bubbleW - 24)
    const h = sizes[i]
    const avatarX = isUser ? w - 24 - 32 : 24
    const bubbleX = isUser ? avatarX - 16 - bubbleW : avatarX + 32 + 16

    ctx.fillStyle = isUser ? '#3b82f6' : '#71717a'
    ctx.beginPath()
    ctx.arc(avatarX + 16, y + 16, 16, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.font = `bold 12px ${fontFamily}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(isUser ? 'U' : 'AI', avatarX + 16, y + 16)
    ctx.textAlign = 'start'
    ctx.textBaseline = 'alphabetic'

    ctx.fillStyle = isUser ? '#3b82f6' : isDark ? '#27272a' : '#f4f4f5'
    roundRect(ctx, bubbleX, y, bubbleW, h, 10)
    ctx.fill()

    ctx.fillStyle = isUser ? '#ffffff' : isDark ? '#fafafa' : '#18181b'
    ctx.font = `14px ${fontFamily}`
    let ty = y + 12 + 16
    for (const line of lines) {
      ctx.fillText(line, bubbleX + 12, ty)
      ty += 22
    }
    y += h + 12
  }

  canvas.toBlob((blob) => {
    if (!blob) {
      alert('生成图片失败')
      return
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(title || 'conversation').replace(
      /[<>:"/\\|?*]/g,
      '_'
    )}.png`
    a.click()
    URL.revokeObjectURL(url)
  }, 'image/png')
}

const currentChatTitle = computed(() => {
  const chat = chatHistory.value.find(c => c.id === conversationId.value)
  return chat ? chat.title : '新对话'
})

function renderMarkdown(text) {
  if (!text) return ''
  const html = marked.parse(text)
  return DOMPurify.sanitize(html)
}

function getAuthHeader() {
  return { Authorization: `Bearer ${token.value}` }
}

function saveChatData() {
  const data = {
    conversationId: conversationId.value,
    chatHistory: chatHistory.value,
    chatMessages: chatMessages.value,
    pinnedChats: pinnedChats.value,
    groups: groups.value
  }
  localStorage.setItem('chatData', JSON.stringify(data))
}

function loadChatData() {
  const saved = localStorage.getItem('chatData')
  if (saved) {
    try {
      const data = JSON.parse(saved)
      chatMessages.value = data.chatMessages || {}
      chatHistory.value = data.chatHistory || []
      conversationId.value = data.conversationId || ''
      pinnedChats.value = data.pinnedChats || []
      groups.value = data.groups || []

      if (chatHistory.value.length === 0) {
        createTempChat()
      } else {
        if (!conversationId.value) {
          conversationId.value = chatHistory.value[0].id
        }
        messages.value = (chatMessages.value[conversationId.value] || []).map(m => {
          if (m.role === 'assistant' && !m.status) {
            return { ...m, status: 'done' }
          }
          return m
        })
      }
    } catch (e) {
      console.error('加载聊天数据失败:', e)
      createTempChat()
    }
  } else {
    createTempChat()
  }
}

function createTempChat() {
  const newId = 'temp-' + Date.now()
  conversationId.value = newId
  messages.value = []
  chatMessages.value[newId] = []
  isTempChat.value = true
}

function createFormalChat(title, preview) {
  const newId = 'conv-' + Date.now()
  const oldId = conversationId.value

  const chatRecord = {
    id: newId,
    title: title || '新对话',
    preview: preview || '',
    createdAt: new Date().toISOString()
  }

  chatHistory.value.unshift(chatRecord)

  if (chatMessages.value[oldId]) {
    chatMessages.value[newId] = [...chatMessages.value[oldId]]
    delete chatMessages.value[oldId]
  }

  conversationId.value = newId
  isTempChat.value = false
  saveChatData()
  return newId
}

function toggleDocumentPanel() {
  showDocumentPanel.value = !showDocumentPanel.value
  if (showDocumentPanel.value) {
    loadDocuments()
  }
}

function toggleTheme() {
  theme.value = theme.value === 'light' ? 'dark' : 'light'
  localStorage.setItem('theme', theme.value)
  document.documentElement.classList.remove(theme.value === 'light' ? 'dark' : 'light')
  document.documentElement.classList.add(theme.value)
}

function logout() {
  token.value = ''
  localStorage.removeItem('token')
  localStorage.removeItem('chatData')
  chatMessages.value = {}
  chatHistory.value = []
  router.push('/login')
}

function newChat() {
  createTempChat()
}

function switchChat(id) {
  const oldId = conversationId.value
  if (isTempChat.value && messages.value.length === 0) {
    delete chatMessages.value[oldId]
  }
  conversationId.value = id
  messages.value = (chatMessages.value[id] || []).map(m => {
    if (m.role === 'assistant' && !m.status) {
      return { ...m, status: 'done' }
    }
    return m
  })
  isTempChat.value = false
  saveChatData()
}

function deleteChat(id) {
  if (!confirm('确定要删除这个对话吗？')) return
  const index = chatHistory.value.findIndex(c => c.id === id)
  if (index !== -1) {
    chatHistory.value.splice(index, 1)
    delete chatMessages.value[id]
    if (conversationId.value === id) {
      if (chatHistory.value.length > 0) {
        conversationId.value = chatHistory.value[0].id
        messages.value = chatMessages.value[conversationId.value] || []
        isTempChat.value = false
      } else {
        createTempChat()
      }
    }
    saveChatData()
  }
}

function toggleSourceExpand(idx) {
  expandedSources.value = { ...expandedSources.value, [idx]: !expandedSources.value[idx] }
}

function stopGeneration() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  loading.value = false
  const lastMsg = messages.value[messages.value.length - 1]
  if (lastMsg && lastMsg.role === 'assistant' && lastMsg.status !== 'done') {
    lastMsg.status = 'done'
    if (!lastMsg.content) lastMsg.content = '（已停止生成）'
    chatMessages.value[conversationId.value] = [...messages.value]
    saveChatData()
  }
}

async function copyAnswer(idx) {
  const msg = messages.value[idx]
  if (!msg || !msg.content) return
  try {
    await navigator.clipboard.writeText(msg.content)
    copiedIdx.value = idx
    setTimeout(() => { copiedIdx.value = -1 }, 2000)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = msg.content
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copiedIdx.value = idx
    setTimeout(() => { copiedIdx.value = -1 }, 2000)
  }
}

async function regenerateAnswer(idx) {
  const msg = messages.value[idx]
  if (!msg || msg.role !== 'assistant') return
  const prevUserMsg = messages.value[idx - 1]
  if (!prevUserMsg || prevUserMsg.role !== 'user') return
  const q = prevUserMsg.content
  messages.value.splice(idx, 1)
  chatMessages.value[conversationId.value] = [...messages.value]
  question.value = q
  await sendQuestion()
}

function startRename(chat) {
  editingChatId.value = chat.id
  editingChatTitle.value = chat.title
}

function saveRename() {
  if (!editingChatId.value) return
  const chat = chatHistory.value.find(c => c.id === editingChatId.value)
  if (chat) {
    chat.title = editingChatTitle.value.trim() || chat.title
    saveChatData()
  }
  editingChatId.value = null
  editingChatTitle.value = ''
}

function cancelRename() {
  editingChatId.value = null
  editingChatTitle.value = ''
}

function exportChat(chatId) {
  const msgs = chatMessages.value[chatId] || []
  const chat = chatHistory.value.find(c => c.id === chatId)
  const title = chat ? chat.title : '对话'
  let md = `# ${title}\n\n`
  md += `> 导出时间: ${new Date().toLocaleString('zh-CN')}\n\n---\n\n`
  for (const msg of msgs) {
    if (msg.role === 'user') {
      md += `## 🧑 用户\n\n${msg.content}\n\n`
    } else {
      md += `## 🤖 助手\n\n${msg.content}\n\n`
      if (msg.sources && msg.sources.length > 0) {
        md += `**参考来源:**\n`
        for (const s of msg.sources) {
          if (s.href) md += `- [${s.title || s.href}](${s.href})\n`
          else if (s.file) md += `- ${s.file}\n`
        }
        md += '\n'
      }
    }
  }
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${title.replace(/[<>:"/\\|?*]/g, '_')}.md`
  a.click()
  URL.revokeObjectURL(url)
}

function isPinned(chatId) {
  return pinnedChats.value.includes(chatId)
}

function openContextMenu(event, chat) {
  contextMenu.visible = false
  contextMenu.chatId = chat.id
  contextMenu.x = event.clientX
  contextMenu.y = event.clientY
  contextMenu.showGroupSubmenu = false
  contextMenu.showExportSubmenu = false

  const maxX = window.innerWidth - 220
  const maxY = window.innerHeight - 280
  if (contextMenu.x > maxX) contextMenu.x = maxX
  if (contextMenu.y > maxY) contextMenu.y = maxY

  contextMenu.visible = true
  document.addEventListener('click', closeContextMenu, { once: true })
  document.addEventListener('contextmenu', closeContextMenu, { once: true })
}

function openContextMenuAtButton(event, chat) {
  contextMenu.visible = false
  contextMenu.chatId = chat.id
  const rect = event.currentTarget.getBoundingClientRect()
  contextMenu.x = rect.right - 192
  contextMenu.y = rect.bottom + 4
  contextMenu.showGroupSubmenu = false
  contextMenu.showExportSubmenu = false

  const maxX = window.innerWidth - 210
  const maxY = window.innerHeight - 300
  if (contextMenu.x > maxX) contextMenu.x = maxX
  if (contextMenu.x < 10) contextMenu.x = 10
  if (contextMenu.y > maxY) contextMenu.y = maxY
  if (contextMenu.y < 10) contextMenu.y = 10

  contextMenu.visible = true
  document.addEventListener('click', closeContextMenu, { once: true })
}

function closeContextMenu() {
  contextMenu.visible = false
  contextMenu.showGroupSubmenu = false
  contextMenu.showExportSubmenu = false
}

async function handleContextAction(action) {
  const chatId = contextMenu.chatId
  if (action === 'export' && !action.startsWith('export_')) {
    contextMenu.showExportSubmenu = !contextMenu.showExportSubmenu
    contextMenu.showGroupSubmenu = false
    return
  }
  closeContextMenu()
  if (!chatId) return

  if (action === 'rename') {
    const chat = chatHistory.value.find(c => c.id === chatId)
    if (chat) startRename(chat)
  } else if (action === 'pin') {
    const idx = pinnedChats.value.indexOf(chatId)
    if (idx >= 0) pinnedChats.value.splice(idx, 1)
    else pinnedChats.value.push(chatId)
    saveChatData()
  } else if (action === 'share') {
    await shareChat(chatId)
  } else if (action === 'export_md') {
    exportChat(chatId)
  } else if (action === 'export_image') {
    exportChatImage(chatId)
  } else if (action === 'delete') {
    deleteChat(chatId)
  } else if (action === 'move') {
    contextMenu.showGroupSubmenu = true
    contextMenu.showExportSubmenu = false
    contextMenu.visible = true
    return
  } else if (action.startsWith('move:')) {
    const group = action.slice(5)
    const chat = chatHistory.value.find(c => c.id === chatId)
    if (!chat) return
    if (group === '__new__') {
      const name = prompt('输入新分组名称：')
      if (!name) return
      if (!groups.value.includes(name)) {
        groups.value.push(name)
        groups.value.sort((a, b) => a.localeCompare(b, 'zh'))
      }
      chat.group = name
    } else {
      chat.group = group
    }
    saveChatData()
  }
}

function handleImageSelect(event) {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    alert('请选择图片文件')
    return
  }
  if (file.size > 20 * 1024 * 1024) {
    alert('图片大小不能超过 20MB')
    return
  }
  if (pendingImage.value && pendingImage.value.url) {
    URL.revokeObjectURL(pendingImage.value.url)
  }
  pendingImage.value = {
    file,
    name: file.name,
    size: file.size,
    url: URL.createObjectURL(file)
  }
  event.target.value = ''
}

function clearPendingImage() {
  if (pendingImage.value && pendingImage.value.url) {
    URL.revokeObjectURL(pendingImage.value.url)
  }
  pendingImage.value = null
}

function formatImageSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

async function sendImageQuestion(q, img) {
  if (isTempChat.value) {
    const title = `[图片] ${q.length > 16 ? q.substring(0, 16) + '...' : q}`
    createFormalChat(title, q.substring(0, 50))
  }

  messages.value.push({
    role: 'user',
    content: q,
    image: img.url,
    image_name: img.name
  })
  chatMessages.value[conversationId.value] = [...messages.value]

  const chat = chatHistory.value.find(c => c.id === conversationId.value)
  if (chat) {
    chat.title = `[图片] ${q.length > 16 ? q.substring(0, 16) + '...' : q}`
    chat.preview = q.substring(0, 50)
  }

  const formData = new FormData()
  formData.append('file', img.file, img.name)
  formData.append('question', q)
  formData.append('conversation_id', conversationId.value)

  pendingImage.value = null
  question.value = ''
  loading.value = true

  const assistantIdx = messages.value.length
  messages.value.push({
    role: 'assistant',
    status: 'thinking',
    content: '',
    sources: [],
    data_source: ''
  })
  chatMessages.value[conversationId.value] = [...messages.value]

  await nextTick()
  scrollToBottom()

  try {
    abortController = new AbortController()
    const response = await fetch(`${API_BASE}/ask/image`, {
      method: 'POST',
      headers: getAuthHeader(),
      body: formData,
      signal: abortController.signal
    })

    if (!response.ok) {
      let errMsg = '请求失败'
      try {
        const errData = await response.json()
        errMsg = errData.detail || errData.answer || errMsg
      } catch (_) {}
      messages.value[assistantIdx].content = errMsg
      messages.value[assistantIdx].data_source = 'error'
      messages.value[assistantIdx].status = 'done'
      chatMessages.value[conversationId.value] = [...messages.value]
      saveChatData()
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'chunk') {
              const cleanChunk = cleanLLMChunk(data.content)
              if (cleanChunk) {
                if (messages.value[assistantIdx].status === 'thinking') {
                  messages.value[assistantIdx].status = 'streaming'
                }
                messages.value[assistantIdx].content += cleanChunk
                chatMessages.value[conversationId.value] = [...messages.value]
                await nextTick()
                scrollToBottom()
              }
            } else if (data.type === 'final') {
              const finalAnswer = cleanLLMChunk(data.content.answer) || messages.value[assistantIdx].content
              messages.value[assistantIdx].content = finalAnswer
              messages.value[assistantIdx].data_source = data.content.data_source || 'vision'
              messages.value[assistantIdx].intent = data.content.intent || ''
              messages.value[assistantIdx].status = 'done'
              chatMessages.value[conversationId.value] = [...messages.value]
            } else if (data.type === 'intent') {
              messages.value[assistantIdx].intent = data.content.intent || ''
              messages.value[assistantIdx].data_source = data.content.intent || messages.value[assistantIdx].data_source
              chatMessages.value[conversationId.value] = [...messages.value]
            }
          } catch (e) {
            console.error('解析图片问答SSE失败:', e)
          }
        }
      }
    }

    await nextTick()
    scrollToBottom()
  } catch (e) {
    messages.value[assistantIdx].content = '图片识别失败: ' + e.message
    messages.value[assistantIdx].data_source = 'error'
    messages.value[assistantIdx].status = 'done'
    chatMessages.value[conversationId.value] = [...messages.value]
  } finally {
    loading.value = false
    saveChatData()
  }
}

async function sendQuestion() {
  const q = question.value.trim()
  const img = pendingImage.value
  if ((!q && !img) || loading.value) return

  if (img) {
    await sendImageQuestion(q || '请描述这张图片的内容。', img)
    return
  }

  if (isTempChat.value) {
    const title = q.length > 20 ? q.substring(0, 20) + '...' : q
    const preview = q.length > 50 ? q.substring(0, 50) + '...' : q
    createFormalChat(title, preview)
  }

  messages.value.push({ role: 'user', content: q })
  chatMessages.value[conversationId.value] = [...messages.value]

  const chat = chatHistory.value.find(c => c.id === conversationId.value)
  if (chat) {
    chat.title = q.length > 20 ? q.substring(0, 20) + '...' : q
    chat.preview = q.length > 50 ? q.substring(0, 50) + '...' : q
  }

  question.value = ''
  loading.value = true

  const assistantIdx = messages.value.length
  messages.value.push({
    role: 'assistant',
    status: 'thinking',
    content: '',
    sources: [],
    data_source: ''
  })
  chatMessages.value[conversationId.value] = [...messages.value]

  await nextTick()
  scrollToBottom()

  try {
    abortController = new AbortController()
    const response = await fetch(`${API_BASE}/ask/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeader()
      },
      body: JSON.stringify({
        question: q,
        conversation_id: conversationId.value,
        force_search: forceSearch.value
      }),
      signal: abortController.signal
    })

    if (!response.ok) {
      const error = await response.json()
      messages.value[assistantIdx].content = error.answer || '请求失败'
      messages.value[assistantIdx].data_source = 'error'
      messages.value[assistantIdx].status = 'done'
      chatMessages.value[conversationId.value] = [...messages.value]
      saveChatData()
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'chunk') {
              const cleanChunk = cleanLLMChunk(data.content)
              if (cleanChunk) {
                if (messages.value[assistantIdx].status === 'thinking') {
                  messages.value[assistantIdx].status = 'streaming'
                }
                messages.value[assistantIdx].content += cleanChunk
                chatMessages.value[conversationId.value] = [...messages.value]
                await nextTick()
                scrollToBottom()
              }
            } else if (data.type === 'sources') {
              messages.value[assistantIdx].sources = data.content || []
              chatMessages.value[conversationId.value] = [...messages.value]
            } else if (data.type === 'final') {
              const finalAnswer = cleanLLMChunk(data.content.answer) || messages.value[assistantIdx].content
              messages.value[assistantIdx].content = finalAnswer
              messages.value[assistantIdx].sources = data.content.sources || []
              messages.value[assistantIdx].data_source = data.content.data_source || ''
              messages.value[assistantIdx].intent = data.content.intent || ''
              messages.value[assistantIdx].detected_language = data.content.detected_language || ''
              messages.value[assistantIdx].needs_clarification = !!data.content.needs_clarification
              messages.value[assistantIdx].status = 'done'
              chatMessages.value[conversationId.value] = [...messages.value]
            } else if (data.type === 'intent') {
              messages.value[assistantIdx].intent = data.content.intent || ''
              messages.value[assistantIdx].data_source = data.content.intent || messages.value[assistantIdx].data_source
              chatMessages.value[conversationId.value] = [...messages.value]
            }
          } catch (e) {
            console.error('解析SSE数据失败:', e)
          }
        }
      }
    }

    await nextTick()
    scrollToBottom()
  } catch (e) {
    messages.value[assistantIdx].content = '请求失败: ' + e.message
    messages.value[assistantIdx].data_source = 'error'
    messages.value[assistantIdx].status = 'done'
    chatMessages.value[conversationId.value] = [...messages.value]
  } finally {
    loading.value = false
    saveChatData()
  }
}

function scrollToBottom() {
  if (chatBox.value) {
    chatBox.value.scrollTop = chatBox.value.scrollHeight
  }
}

async function handleFileUpload(event) {
  const files = event.target.files
  if (!files || files.length === 0) return

  const formData = new FormData()
  for (let i = 0; i < files.length; i++) {
    formData.append('files', files[i])
  }

  loading.value = true
  try {
    const res = await fetch(`${API_BASE}/upload-multiple`, {
      method: 'POST',
      headers: getAuthHeader(),
      body: formData
    })

    const data = await res.json()
    if (!res.ok) {
      alert(data.detail || '上传失败')
      return
    }

    const results = data.results || []
    const successCount = results.filter(r => r.success).length
    const failCount = results.length - successCount
    const failMessages = results.filter(r => !r.success).map(r => `${r.file}: ${r.error}`).join('\n')
    if (failCount > 0) {
      alert(`上传完成: ${successCount} 成功, ${failCount} 失败\n${failMessages}`)
    } else {
      alert(`上传成功: ${successCount} 个文件`)
    }
    await loadDocuments()
    await refreshStats()
  } catch (e) {
    alert('上传失败: ' + e.message)
  } finally {
    loading.value = false
    event.target.value = ''
  }
}

async function loadDocuments() {
  try {
    const res = await fetch(`${API_BASE}/documents`, { headers: getAuthHeader() })
    const data = await res.json()
    documents.value = data.documents || []
  } catch (e) {
    console.error('加载文档列表失败:', e)
  }
}

async function deleteDocument(filename) {
  if (!confirm(`确定要删除 ${filename} 吗？`)) return

  try {
    const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
      headers: getAuthHeader()
    })
    if (!res.ok) {
      const data = await res.json()
      alert(data.detail || '删除失败')
      return
    }
    await loadDocuments()
    await refreshStats()
    alert(`文件 ${filename} 删除成功`)
  } catch (e) {
    alert('删除失败: ' + e.message)
  }
}

async function refreshStats() {
  try {
    const res = await fetch(`${API_BASE}/stats`, { headers: getAuthHeader() })
    const data = await res.json()
    stats.value = data
  } catch (e) {
    console.error('获取统计信息失败:', e)
  }
}

function getFileIcon(ext) {
  const icons = {
    '.txt': '📄',
    '.pdf': '📕',
    '.md': '📝',
    '.docx': '📘',
    '.doc': '📘',
    '.pptx': '📗',
    '.ppt': '📗',
    '.jpg': '🖼️',
    '.jpeg': '🖼️',
    '.png': '🖼️',
    '.bmp': '🖼️',
    '.tiff': '🖼️'
  }
  return icons[ext] || '📄'
}

function formatSize(bytes) {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return '未知大小'
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const _INTERNAL_NOISE_RE = /(content=""|additional_kwargs=|response_metadata=|usage_metadata=|tool_calls=\[|invalid_tool_calls=|input_token_details|output_token_details|tool_call_chunks=|chunk_position=|id='?-?lc_)/g
function cleanLLMChunk(text) {
  if (text == null) return ''
  if (typeof text !== 'string') {
    if (text && typeof text === 'object' && typeof text.text === 'string') text = text.text
    else if (text && typeof text === 'object' && typeof text.content === 'string') text = text.content
    else return ''
  }
  if (!text) return ''
  if (_INTERNAL_NOISE_RE.test(text)) {
    text = text.replace(_INTERNAL_NOISE_RE, '')
  }
  return text.replace(/^\s*>\s*注[:：].*$/gm, '').trim()
}

function isWebSource(msg) {
  if (!msg || !msg.sources || msg.sources.length === 0) return false
  if (msg.data_source && (msg.data_source.includes('web') || msg.data_source.includes('search'))) return true
  // 通过任一来源的 href 特征判断
  return msg.sources.some(s => s && (s.href || s.url) && (s.title || s.snippet))
}

function getDataSourceLabel(source) {
  const labels = {
    rag: '知识库',
    rag_search: '知识库+搜索',
    llm: '大模型',
    llm_fallback: '大模型(兜底)',
    general: '通用对话',
    general_search: '搜索增强',
    web_search: '联网搜索',
    web_search_fallback: '联网搜索(兜底)',
    task: '任务执行',
    file_qa: '文件问答',
    file_qa_miss: '文件未找到',
    long_text: '长文本处理',
    long_text_summary: '长文本摘要',
    long_text_translate: '长文本翻译',
    long_text_qa: '长文本问答',
    clarification: '需澄清',
    error: '错误'
  }
  return labels[source] || source
}

function getIntentLabel(intent) {
  const labels = {
    kb_qa: '知识库问答',
    general: '通用对话',
    long_text: '长文本',
    file_qa: '文件问答',
    task: '任务',
    clarification_needed: '需澄清'
  }
  return labels[intent] || intent
}

function openLongText(op) {
  longtextOp.value = op
  longtextInput.value = ''
  longtextResult.value = ''
  showLongTextPanel.value = true
}

async function loadProfile() {
  try {
    const res = await fetch(`${API_BASE}/profile`, { headers: getAuthHeader() })
    const data = await res.json()
    profileForm.value.display_name = data.display_name || ''
    profileForm.value.profession = data.profession || ''
    profileForm.value.language = data.language || 'auto'
    profileFormFactsText.value = (data.facts || []).join('\n')
    profileFormPrefsText.value = JSON.stringify(data.preferences || {}, null, 2)
  } catch (e) {
    console.error('加载用户画像失败:', e)
  }
}

async function saveProfile() {
  const facts = profileFormFactsText.value.split('\n').map(s => s.trim()).filter(Boolean)
  let preferences = {}
  try { preferences = JSON.parse(profileFormPrefsText.value || '{}') } catch (e) { alert('偏好 JSON 格式错误'); return }
  try {
    const res = await fetch(`${API_BASE}/profile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ ...profileForm.value, facts, preferences })
    })
    if (!res.ok) { alert('保存失败'); return }
    alert('用户画像已保存')
  } catch (e) { alert('保存失败: ' + e.message) }
}

async function runLongText() {
  if (!longtextInput.value.trim() || longtextLoading.value) return
  longtextLoading.value = true
  longtextResult.value = ''
  try {
    const body = { text: longtextInput.value, operation: longtextOp.value, max_chars: 600 }
    if (longtextOp.value === 'translate') body.target_language = longtextTargetLang.value || 'English'
    if (longtextOp.value === 'qa') body.question = longtextQuestion.value
    const res = await fetch(`${API_BASE}/long-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(body)
    })
    const data = await res.json()
    if (!res.ok) { alert(data.detail || '执行失败'); return }
    longtextResult.value = data.result || '（无结果）'
  } catch (e) { alert('执行失败: ' + e.message) }
  finally { longtextLoading.value = false }
}

async function fetchExampleQuestions() {
  try {
    const res = await fetch(`${API_BASE}/example-questions`, { headers: getAuthHeader() })
    if (!res.ok) return
    const data = await res.json()
    exampleQuestions.value = data.questions || []
  } catch (e) {
    console.error('获取示例问题失败:', e)
  }
}

async function askExampleQuestion(q) {
  question.value = q
  await sendQuestion()
}

async function submitFeedback(msgIdx, rating) {
  const msg = messages.value[msgIdx]
  if (!msg) return
  
  feedbackState.value = { ...feedbackState.value, [msgIdx]: rating }
  
  try {
    await fetch(`${API_BASE}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({
        message_id: `msg-${msgIdx}-${Date.now()}`,
        conversation_id: conversationId.value,
        rating: rating,
      })
    })
  } catch (e) {
    console.error('提交反馈失败:', e)
  }
}

function enterBatchMode() {
  batchMode.value = true
  selectedChatIds.value.clear()
}

function exitBatchMode() {
  batchMode.value = false
  selectedChatIds.value.clear()
}

async function batchDeleteChats() {
  if (selectedChatIds.value.size === 0) return
  if (!confirm(`确定要删除选中的 ${selectedChatIds.value.size} 个对话吗？`)) return
  
  try {
    const res = await fetch(`${API_BASE}/conversations/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ conversation_ids: Array.from(selectedChatIds.value) })
    })
    if (!res.ok) { alert('删除失败'); return }
    
    selectedChatIds.value.forEach(id => {
      const index = chatHistory.value.findIndex(c => c.id === id)
      if (index !== -1) chatHistory.value.splice(index, 1)
      delete chatMessages.value[id]
    })
    
    if (selectedChatIds.value.has(conversationId.value)) {
      if (chatHistory.value.length > 0) {
        conversationId.value = chatHistory.value[0].id
        messages.value = chatMessages.value[conversationId.value] || []
        isTempChat.value = false
      } else {
        createTempChat()
      }
    }
    
    selectedChatIds.value.clear()
    exitBatchMode()
    saveChatData()
  } catch (e) {
    alert('删除失败: ' + e.message)
  }
}

function toggleSelectChat(id, event) {
  if (event) event.stopPropagation()
  if (selectedChatIds.value.has(id)) {
    selectedChatIds.value.delete(id)
  } else {
    selectedChatIds.value.add(id)
  }
}

function toggleSelectAll() {
  if (selectedChatIds.value.size === chatHistory.value.length) {
    selectedChatIds.value.clear()
  } else {
    chatHistory.value.forEach(c => selectedChatIds.value.add(c.id))
  }
}

async function handleLongTextPaste(e) {
  const text = e.clipboardData?.getData('text') || ''
  if (text.length > 500) {
    longTextPromptText.value = text
    longTextPromptVisible.value = true
    e.preventDefault()
  }
}

function handleLongTextPromptAction(action) {
  longtextInput.value = longTextPromptText.value
  if (action === 'summary') {
    longtextOp.value = 'summary'
    showLongTextPanel.value = true
  } else if (action === 'translate') {
    longtextOp.value = 'translate'
    showLongTextPanel.value = true
  }
  longTextPromptVisible.value = false
  longTextPromptText.value = ''
}

function formatTime() {
  const now = new Date()
  return now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function checkBackend() {
  try {
    const res = await fetch(`${API_BASE}/stats`, {
      method: 'GET',
      headers: getAuthHeader(),
      signal: AbortSignal.timeout(3000)
    })
    backendOffline.value = !res.ok
  } catch (e) {
    backendOffline.value = true
  }
}

onMounted(async () => {
  loadChatData()
  await checkBackend()
  if (!backendOffline.value) {
    await refreshStats()
    await loadDocuments()
    await loadProfile()
    await fetchExampleQuestions()
  }
  backendCheckTimer = setInterval(checkBackend, 10000)
})

onUnmounted(() => {
  if (backendCheckTimer) {
    clearInterval(backendCheckTimer)
    backendCheckTimer = null
  }
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  if (typeof window !== 'undefined' && window.speechSynthesis) {
    window.speechSynthesis.cancel()
  }
  try {
    recognition && recognition.abort && recognition.abort()
  } catch (e) {}
  try {
    recognition && recognition.stop && recognition.stop()
  } catch (e) {}
})
</script>

<style scoped>
.chat-main {
  font-family: var(--font-body);
  background-color: var(--color-bg);
}

.backend-offline-banner {
  background-color: var(--state-error-bg);
  color: var(--state-error);
  border-bottom: 1px solid var(--state-error);
}

/* ===== Sidebar ===== */
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

.chat-list::-webkit-scrollbar { width: 4px; }
.chat-list::-webkit-scrollbar-track { background: transparent; }
.chat-list::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

.chat-item {
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
  position: relative;
}

.chat-item:hover {
  background-color: var(--color-surface-secondary);
}

.chat-item.active {
  background-color: var(--color-primary-lighter);
}

.chat-item-icon {
  color: var(--color-text-tertiary);
}

.chat-item.active .chat-item-icon {
  color: var(--color-primary);
}

.chat-item.pinned {
  background-color: var(--color-primary-lighter);
}

.chat-item.pinned .pinned-icon {
  color: var(--color-primary);
}

.chat-item.pinned-indicator::before {
  content: '';
  position: absolute;
  left: -4px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 16px;
  background-color: var(--color-primary);
  border-radius: 2px;
}

.chat-item-title {
  color: var(--color-text-primary);
  font-weight: 400;
  font-size: 0.8125rem;
  line-height: 1.4;
}

.chat-item.active .chat-item-title {
  font-weight: 500;
}

.chat-section-header {
  font-weight: 500;
  letter-spacing: 0.02em;
  user-select: none;
}

.chat-tooltip {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 8px);
  transform: translateX(-50%);
  background: var(--color-surface);
  color: var(--color-text-primary);
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  border: 1px solid var(--color-border);
  transition: opacity 0.15s;
}
.chat-item:hover .chat-tooltip {
  opacity: 1;
}
.chat-tooltip::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent;
  border-top-color: var(--color-surface);
}

.delete-chat-btn {
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  transition: background-color 0.15s;
}

.delete-chat-btn:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.chat-menu-btn {
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  transition: color 0.15s, background-color 0.15s;
  cursor: pointer;
  border: none;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}

.chat-menu-btn:hover {
  color: var(--color-primary);
  background-color: var(--color-border);
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

.sidebar-nav-btn .nav-icon {
  color: var(--color-text-tertiary);
}

.sidebar-nav-btn.logout:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

/* ===== Context Menu ===== */
.context-menu {
  position: fixed;
  z-index: 1000;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12), 0 4px 12px rgba(0, 0, 0, 0.06);
  padding: 6px;
  min-width: 200px;
  animation: contextMenuFadeIn 0.12s ease-out;
  font-size: 13px;
  user-select: none;
}

@keyframes contextMenuFadeIn {
  from { opacity: 0; transform: translateY(-4px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  color: var(--color-text-primary);
  transition: background-color 0.12s, color 0.12s;
  white-space: nowrap;
}

.context-menu-item:hover {
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.context-menu-item svg {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.context-menu-item:hover svg {
  color: var(--color-primary);
}

.context-menu-item.danger {
  color: var(--state-error);
}

.context-menu-item.danger:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.context-menu-item.danger svg {
  color: var(--state-error);
}

.context-menu-item.danger:hover svg {
  color: var(--state-error);
}

.context-menu-item.disabled {
  color: var(--color-text-tertiary);
  cursor: not-allowed;
}

.context-menu-item.disabled:hover {
  background-color: transparent;
  color: var(--color-text-tertiary);
}

.context-menu-item.has-submenu {
  position: relative;
}

.context-menu-item .submenu-arrow {
  margin-left: auto;
  color: var(--color-text-tertiary);
}

.context-menu-divider {
  height: 1px;
  background-color: var(--color-border);
  margin: 5px 4px;
}

.context-submenu {
  position: fixed;
  z-index: 1001;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12), 0 4px 12px rgba(0, 0, 0, 0.06);
  padding: 6px;
  min-width: 180px;
  animation: contextMenuFadeIn 0.12s ease-out;
  font-size: 13px;
}

/* Pin indicator on chat items */
.chat-item.pinned-indicator::before {
  content: '';
  position: absolute;
  left: -4px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 16px;
  background-color: var(--color-primary);
  border-radius: 2px;
}

/* ===== Chat section ===== */
.chat-section {
  background-color: var(--color-bg);
}

/* Mobile sidebar drawer */
.mobile-sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 999;
  animation: fadeIn 0.15s ease-out;
}

.mobile-sidebar-drawer {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: 280px;
  max-width: 85vw;
  background-color: var(--color-surface);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  animation: slideInLeft 0.2s ease-out;
  box-shadow: 4px 0 24px rgba(0, 0, 0, 0.1);
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes slideInLeft {
  from { transform: translateX(-100%); }
  to { transform: translateX(0); }
}

.chat-header {
  background-color: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.header-menu-btn {
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.header-menu-btn:hover {
  background-color: var(--color-surface-secondary);
}

.chat-header-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}

.stats-text {
  color: var(--color-text-tertiary);
}

.refresh-btn {
  color: var(--color-text-tertiary);
  transition: color 0.15s;
}

.refresh-btn:hover {
  color: var(--color-primary);
}

/* ===== Messages area ===== */
.messages-area {
  background-color: var(--color-bg);
}

.empty-state {
  display: flex;
  height: 100%;
  padding: 40px 20px;
}

.empty-icon {
  background-color: var(--color-surface-secondary);
  border-radius: var(--radius-full);
  color: var(--color-text-tertiary);
}

.empty-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}

.empty-desc {
  color: var(--color-text-tertiary);
}

/* ===== Quick action cards ===== */
.quick-action-cards {
  width: 100%;
}

.action-cards-title {
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.quick-action-card {
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-secondary);
  transition: all 0.15s;
}

.quick-action-card:hover {
  border-color: var(--color-primary);
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.action-card-icon {
  color: var(--color-text-tertiary);
}

.quick-action-card:hover .action-card-icon {
  color: var(--color-primary);
}

/* ===== Example questions ===== */
.example-questions {
  padding: 0;
}

.example-title {
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.example-question-btn {
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  transition: all 0.15s;
}

.example-question-btn:hover {
  background-color: var(--color-primary-lighter);
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-item .avatar {
  border-radius: var(--radius-full);
}

.message-item.user .avatar {
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.message-item .assistant-avatar {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
}

.message-item .bubble {
  border-radius: var(--radius-lg);
  line-height: var(--leading-normal);
}

.message-item.user .bubble {
  background-color: var(--color-primary);
  color: var(--color-text-inverse);
}

.message-item .assistant-bubble {
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
}

.message-text {
  color: inherit;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-item.user .message-text {
  color: var(--color-text-inverse);
}

.message-time {
  color: var(--color-text-tertiary);
  opacity: 0.8;
}

.message-item.user .message-time {
  color: rgba(255, 255, 255, 0.65);
}

/* Thinking indicator */
.thinking-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.typing-dots {
  display: flex;
  gap: 4px;
}

.typing-dots span {
  width: 8px;
  height: 8px;
  background: var(--color-primary);
  border-radius: 50%;
  animation: typing 1.4s infinite;
}

.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }

.typing-text {
  color: var(--color-text-tertiary);
  font-size: var(--text-sm);
}

/* Source section */
.source-section {
  border-top: 1px solid var(--color-border-light);
}

.source-toggle-btn {
  color: var(--color-primary);
  font-weight: 500;
}

.source-content {
  background-color: var(--color-surface-secondary);
  border-radius: var(--radius-md);
}

.source-item {
  margin-bottom: 8px;
}

.source-item:last-child {
  margin-bottom: 0;
}

.source-file {
  color: var(--color-primary);
  font-weight: 500;
}

.source-preview {
  color: var(--color-text-tertiary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Web source styles */
.web-source-item {
  padding: 6px 0;
  border-bottom: 1px solid var(--color-border-light);
}
.web-source-item:last-child {
  border-bottom: none;
}
.web-source-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-primary);
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
}
.web-source-title:hover {
  text-decoration: underline;
}
.source-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  background: var(--color-primary-lighter);
  color: var(--color-primary);
  border-radius: 50%;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}
.source-title-text {
  flex: 1;
  word-break: break-word;
}
.external-link-icon {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}
.source-preview-text {
  color: var(--color-text-tertiary);
  line-height: 1.5;
  word-break: break-word;
}
.source-engine {
  color: var(--color-text-tertiary);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Message meta */
.message-meta .data-source-badge {
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
  border-radius: var(--radius-full);
  font-weight: 500;
}

.data-source-badge.rag {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.data-source-badge.llm {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}

.data-source-badge.llm_fallback {
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.data-source-badge.error {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.intent-badge {
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
  border-radius: var(--radius-full);
  font-weight: 500;
}

.intent-badge.intent-kb_qa { background: var(--state-success-bg); color: var(--state-success); }
.intent-badge.intent-general { background: var(--state-info-bg); color: var(--state-info); }
.intent-badge.intent-long_text { background: var(--state-warning-bg); color: var(--state-warning); }
.intent-badge.intent-file_qa { background: var(--color-primary-lighter); color: var(--color-primary); }
.intent-badge.intent-task { background: var(--state-error-bg); color: var(--state-error); }
.intent-badge.intent-clarification_needed { background: var(--state-error-bg); color: var(--state-error); }

/* ===== Input area ===== */
.input-area {
  background-color: var(--color-surface);
  border-top: 1px solid var(--color-border);
}

.toolbar-btn {
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s, color 0.15s;
  border: none;
  background: transparent;
  cursor: pointer;
}

.toolbar-btn:hover {
  background-color: var(--color-surface-secondary);
}

.toolbar-btn.active {
  color: var(--color-primary);
  background-color: var(--color-primary-lighter);
}

.input-wrapper {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.input-wrapper:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-lighter);
}

.image-preview-bar {
  background-color: var(--color-surface-variant, rgba(0,0,0,0.04));
  border: 1px solid var(--color-border);
}

.question-textarea {
  color: var(--color-text-primary);
  line-height: var(--leading-normal);
  min-height: 22px;
  font-family: var(--font-body);
}

.question-textarea::placeholder {
  color: var(--color-text-tertiary);
}

.send-btn {
  background-color: var(--color-primary);
  color: var(--color-text-inverse);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.send-btn:hover:not(:disabled) {
  background-color: var(--color-primary-dark);
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.stop-btn {
  background-color: var(--state-error);
  color: var(--color-text-inverse);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.stop-btn:hover {
  background-color: var(--state-error-bg);
}

.msg-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  transition: color 0.15s, background-color 0.15s;
  cursor: pointer;
  border: none;
  background: transparent;
}

.msg-action-btn:hover {
  color: var(--color-primary);
  background-color: var(--color-hover);
}

.chat-action-btn {
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  transition: color 0.15s, background-color 0.15s;
  cursor: pointer;
  border: none;
  background: transparent;
}

.chat-action-btn:hover {
  color: var(--color-primary);
  background-color: var(--color-hover);
}

.rename-input {
  background-color: var(--color-input-bg);
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  color: var(--color-text-primary);
}

.quick-tools .quick-pill {
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  background-color: transparent;
  border-radius: var(--radius-md);
  transition: border-color 0.15s, color 0.15s;
}

.quick-tools .quick-pill:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.quick-tools .quick-pill:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.input-footer-hint {
  color: var(--color-text-tertiary);
}

/* ===== Voice input ===== */
.voice-btn {
  color: var(--color-text-tertiary);
  border-radius: var(--radius-md);
  transition: color 0.15s, background-color 0.15s;
  border: none;
  background: transparent;
  cursor: pointer;
}
.voice-btn:hover:not(:disabled) {
  color: var(--color-primary);
  background-color: var(--color-hover);
}
.voice-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.voice-btn.listening {
  color: #ef4444;
  background-color: rgba(239, 68, 68, 0.1);
  animation: pulse 1.4s infinite;
}
@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.1); }
}

/* ===== Share url ===== */
.share-url-box input {
  font-family: ui-monospace, "Cascadia Code", monospace;
}

/* ===== Modal ===== */
.modal-overlay-wrap {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal-content {
  position: relative;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 800px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-elevated);
}

.modal-header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.modal-close-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s, color 0.15s;
}

.modal-close-btn:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.modal-footer {
  padding: 16px 24px;
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: center;
}

/* Document grid */
.empty-documents {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: var(--color-text-tertiary);
}

.empty-documents p {
  margin-top: 16px;
  font-size: var(--text-sm);
}

.empty-doc-icon {
  color: var(--color-text-tertiary);
  opacity: 0.5;
}

.document-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}

.document-card {
  background: var(--color-surface-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  transition: border-color 0.15s;
}

.document-card:hover {
  border-color: var(--color-primary);
}

.doc-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

.doc-info {
  flex: 1;
  margin-bottom: 12px;
}

.doc-name {
  color: var(--color-text-primary);
  font-size: var(--text-sm);
  font-weight: 500;
  margin-bottom: 4px;
  word-break: break-all;
}

.doc-size {
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
}

.doc-delete-btn {
  width: 100%;
  padding: 6px 12px;
  background: var(--state-error-bg);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--state-error);
  font-size: var(--text-xs);
  cursor: pointer;
  transition: background-color 0.15s;
}

.doc-delete-btn:hover {
  opacity: 0.8;
}

.upload-btn-large {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  background: var(--color-primary);
  color: var(--color-text-inverse);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: var(--text-sm);
  font-weight: 500;
  transition: background-color 0.15s;
}

.upload-btn-large:hover {
  background: var(--color-primary-dark);
}

.upload-btn-large input {
  display: none;
}

/* ===== Profile panel ===== */
.profile-modal {
  max-width: 520px;
}

.profile-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.form-label {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  margin-top: 6px;
  font-weight: 500;
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  background: var(--color-surface-secondary);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-family: var(--font-body);
  transition: border-color 0.15s, box-shadow 0.15s;
  overflow-y: auto;
}

.form-input:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-lighter);
}

.profile-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.btn-primary {
  flex: 1;
  padding: 10px 16px;
  background: var(--color-primary);
  color: var(--color-text-inverse);
  border: none;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background-color 0.15s;
}

.btn-primary:hover:not(:disabled) {
  background: var(--color-primary-dark);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  flex: 1;
  padding: 10px 16px;
  background: var(--color-surface-secondary);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  cursor: pointer;
  transition: background-color 0.15s;
}

.btn-secondary:hover {
  background: var(--color-border);
}

.hint-text {
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
  margin-top: 12px;
  line-height: 1.5;
}

/* ===== Long text panel ===== */
.longtext-modal {
  max-width: 720px;
}

.longtext-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

.tab-btn {
  flex: 1;
  padding: 8px 12px;
  background: var(--color-surface-secondary);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: var(--text-sm);
  transition: all 0.15s;
}

.tab-btn.active {
  background: var(--color-primary);
  color: var(--color-text-inverse);
  border-color: var(--color-primary);
}

.longtext-result {
  margin-top: 12px;
  background: var(--color-surface-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 12px;
  max-height: 320px;
  overflow-y: auto;
}

.longtext-result pre {
  color: var(--color-text-primary);
  font-size: var(--text-sm);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-body);
  margin: 0;
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ===== Search box ===== */
.search-box input {
  color: var(--color-text);
}
.search-box input::placeholder {
  color: var(--color-text-muted);
}

/* ===== Chat item selection ===== */
.chat-item.selected {
  background-color: var(--color-accent-light);
}

/* ===== Message actions ===== */
.msg-actions {
  opacity: 0;
  transition: opacity 0.2s;
}
.message:hover .msg-actions {
  opacity: 1;
}
.msg-action-btn {
  color: var(--color-text-muted);
  transition: color 0.2s;
}
.msg-action-btn:hover {
  color: var(--color-accent);
}
.msg-action-btn.active {
  color: var(--color-accent);
}

/* ===== Data source badge ===== */
.data-source-badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
}
.data-source-badge.kb {
  background-color: var(--color-accent-light);
  color: var(--color-accent);
}
.data-source-badge.web {
  background-color: var(--color-success-light);
  color: var(--color-success);
}
.data-source-badge.mixed {
  background-color: var(--color-warning-light);
  color: var(--color-warning);
}

/* ===== Markdown body rendering ===== */
.markdown-body {
  line-height: 1.5;
  word-break: break-word;
}
.markdown-body :deep(p) {
  margin: 0 0 0.4em 0;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  margin: 0.8em 0 0.4em;
  font-weight: 600;
  line-height: 1.3;
}
.markdown-body :deep(h1) { font-size: 1.4em; }
.markdown-body :deep(h2) { font-size: 1.25em; }
.markdown-body :deep(h3) { font-size: 1.1em; }
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) { font-size: 1em; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.4em 0 0.6em;
  padding-left: 1.6em;
}
.markdown-body :deep(li) {
  margin: 0.2em 0;
}
.markdown-body :deep(li > ul),
.markdown-body :deep(li > ol) {
  margin: 0.2em 0;
}
.markdown-body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 0.2em 0.9em;
  border-left: 3px solid var(--color-primary);
  background-color: var(--color-hover);
  color: var(--color-text-secondary);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.markdown-body :deep(blockquote p) {
  margin: 0.2em 0;
}
.markdown-body :deep(code) {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.88em;
  padding: 0.15em 0.4em;
  background-color: var(--color-hover);
  border-radius: var(--radius-sm);
  color: var(--color-primary);
}
.markdown-body :deep(pre) {
  margin: 0.6em 0;
  padding: 0.9em 1em;
  background-color: var(--color-code-bg, #1e1e2e);
  border-radius: var(--radius-md);
  overflow-x: auto;
  line-height: 1.5;
}
.markdown-body :deep(pre code) {
  padding: 0;
  background: transparent;
  color: var(--color-code-text, #e0e0e0);
  font-size: 0.85em;
  border-radius: 0;
}
.markdown-body :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.markdown-body :deep(a:hover) {
  border-bottom-color: var(--color-primary);
}
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.6em 0;
  font-size: 0.9em;
  display: block;
  overflow-x: auto;
}
.markdown-body :deep(thead) {
  background-color: var(--color-hover);
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 0.4em 0.7em;
  text-align: left;
}
.markdown-body :deep(th) {
  font-weight: 600;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 1em 0;
}
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-sm);
}
.markdown-body :deep(strong) {
  font-weight: 600;
}
.markdown-body :deep(em) {
  font-style: italic;
}
</style>
