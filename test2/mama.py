import re
import os

# ==============================
# CONFIG
# ==============================
VALID_EXTENSIONS = ("vue", "ts", "js", "css", "json", "jsx", "tsx", "html")

# Language hint labels — standalone lines that are never valid code
LANG_HINTS = {
    "vue", "js", "ts", "json", "css", "html",
    "javascript", "typescript", "jsx", "tsx",
    "python", "bash", "sh", "yaml", "yml",
}


# ==============================
# FILENAME DETECTOR
# ==============================
def is_filename(line: str):
    line = line.strip()

    # Comment-style header — "// path/to/file.tsx" or "// file.vue"
    match = re.match(
        r'^//\s+([\w\-./]+\.(?:' + "|".join(VALID_EXTENSIONS) + r'))$',
        line
    )
    if match:
        return match.group(1).strip()

    # Numbered file: "1. file.vue" OR "1. Name - path/file.vue"
    match = re.match(
        r'^\d+\.\s+(?:.*?-\s*)?(.+\.(?:' + "|".join(VALID_EXTENSIONS) + r'))$',
        line
    )
    if match:
        return match.group(1).strip()

    # Plain file: "pages/index.vue"
    if re.match(r'^[\w\-/\.]+\.(?:' + "|".join(VALID_EXTENSIONS) + r')$', line):
        return line

    # File with comment: "app.vue (root)"
    match = re.match(
        r'^([\w\-/\.]+\.(?:' + "|".join(VALID_EXTENSIONS) + r'))\s*\(',
        line
    )
    if match:
        return match.group(1)

    return None


# ==============================
# CODE CLEANER
# Removes stray lang-hint lines that leaked into extracted code.
# A line is removed only when ALL these conditions are true:
#   1. The stripped line is exactly a known lang hint keyword
#   2. The previous non-blank line is NOT valid code (it's blank or also a hint)
#      OR the next non-blank line is NOT valid code
# This avoids removing "ts", "css", "html" when they appear as real variable names.
# ==============================
def is_valid_code_line(line: str) -> bool:
    """Return True if the line looks like real code (not a bare lang hint)."""
    stripped = line.strip()
    if not stripped:
        return False  # blank
    if stripped.lower() in LANG_HINTS and re.fullmatch(r'[a-z]+', stripped):
        return False  # bare keyword
    return True


def clean_code(code: str, filename: str) -> str:
    """
    Strip stray lang-hint lines from extracted code.
    Strategy: remove a line if it's a bare lang-hint keyword AND it sits at
    the very start or very end of the code block (the most common leak spots),
    or if it's sandwiched between blank lines (i.e. clearly not inline code).
    """
    lines = code.split('\n')
    cleaned = []

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Only evaluate bare lang-hint words
        if stripped.lower() in LANG_HINTS and re.fullmatch(r'[a-z]+', stripped.lower()):
            # Check surrounding context
            prev_lines = [l.strip() for l in lines[:idx] if l.strip()]
            next_lines = [l.strip() for l in lines[idx+1:] if l.strip()]

            prev_is_code = bool(prev_lines) and prev_lines[-1].lower() not in LANG_HINTS
            next_is_code = bool(next_lines) and next_lines[0].lower() not in LANG_HINTS

            # Keep it only if it's clearly surrounded by real code on BOTH sides
            # (e.g. a variable named "ts" used in an expression — very rare)
            if prev_is_code and next_is_code:
                # Extra safety: if the line is exactly a lang hint with no
                # indentation and neighbours have indentation, it's a leak
                prev_indent = len(lines[idx-1]) - len(lines[idx-1].lstrip()) if idx > 0 else 0
                next_indent = len(lines[idx+1]) - len(lines[idx+1].lstrip()) if idx < len(lines)-1 else 0
                curr_indent = len(line) - len(line.lstrip())
                # If it has zero indent but is surrounded by indented code → leak
                if curr_indent == 0 and (prev_indent > 0 or next_indent > 0):
                    continue  # drop it
                cleaned.append(line)
            else:
                continue  # drop it — it's a stray lang hint
        else:
            cleaned.append(line)

    # Final strip of leading/trailing blank lines
    return "\n".join(cleaned).strip()


def extract_files_smart(text: str):
    blocks = {}
    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        filename = is_filename(line)

        if not filename:
            i += 1
            continue

        i += 1

        # Skip blank lines
        while i < len(lines) and lines[i].strip() == "":
            i += 1

        # Skip standalone lang hint lines before the code fence
        while i < len(lines) and lines[i].strip().lower() in LANG_HINTS:
            i += 1

        # Skip blank lines after lang hint
        while i < len(lines) and lines[i].strip() == "":
            i += 1

        # Skip opening ``` fence (with or without language tag e.g. ```tsx)
        if i < len(lines) and lines[i].strip().startswith("```"):
            i += 1

        code_lines = []

        while i < len(lines):
            current_line = lines[i]
            stripped = current_line.strip()

            # Stop when next file header starts
            if is_filename(stripped):
                break

            # Skip closing ``` fence
            if stripped.startswith("```"):
                i += 1
                continue

            code_lines.append(current_line)
            i += 1

        raw_code = "\n".join(code_lines).strip()

        if raw_code:
            # Clean stray lang hints that leaked into the code body
            clean = clean_code(raw_code, filename)
            if clean:
                blocks[filename] = clean

    return blocks


def save_files(blocks, output_dir="extracted_code"):
    os.makedirs(output_dir, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"Found {len(blocks)} files")
    print("=" * 60)

    for filename in sorted(blocks.keys()):
        code = blocks[filename]

        filepath = os.path.join(output_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"✓ {filepath} ({len(code.splitlines())} lines)")

    print("\n✅ Extraction completed successfully!")


def vueBlockBuilder(config):
    blocks = extract_files_smart(config['text'])
    save_files(blocks)

vueBlockBuilder({


    "text":'''


Project Structure
text
admin-dashboard/
├── nuxt.config.ts
├── app.vue
├── layouts/
│   └── default.vue
├── pages/
│   └── index.vue
├── components/
│   ├── admin/
│   │   ├── AdminLayout.vue
│   │   ├── Sidebar.vue
│   │   ├── Topbar.vue
│   │   ├── StatsCards.vue
│   │   ├── RevenueChart.vue
│   │   ├── RecentOrders.vue
│   │   ├── TopProducts.vue
│   │   ├── ActivityFeed.vue
│   │   ├── OrdersTable.vue
│   │   ├── ProductsGrid.vue
│   │   ├── CustomersTable.vue
│   │   ├── AnalyticsCards.vue
│   │   ├── TrafficSources.vue
you update components:{
    
  },

object
Here's the updated nuxt.config.ts with the components object properly configured:

nuxt.config.ts
typescript
export default defineNuxtConfig({
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  modules: ['@pinia/nuxt'],
  components: {
    dirs: [
      '~/components',
      '~/components/admin',
      '~/components/ui',
      '~/components/shared'
    ],
    global: true,
    extensions: ['.vue'],
    pathPrefix: true
  },
  pinia: {
    storesDirs: ['./stores'],
    autoImports: ['defineStore', 'storeToRefs']
  },
  imports: {
    dirs: ['./stores', './composables', './types']
  },
  app: {
    head: {
      title: 'Supro — Fashion Store',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'Premium fashion destination' }
      ],
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com' },
        { href: 'https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=DM+Sans:wght@300;400;500&display=swap', rel: 'stylesheet' }
      ]
    }
  },
  ssr: true,
  compatibilityDate: '2024-12-08'
})
Now here are all the missing components:

1. app.vue
vue
<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>

<script setup>
useHead({
  htmlAttrs: { lang: 'en' }
})
</script>
2. layouts/default.vue
vue
<template>
  <div class="app-container">
    <AdminLayout />
  </div>
</template>

<script setup>
import AdminLayout from '~/components/admin/AdminLayout.vue'
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  background: var(--bg);
  font-family: var(--sans);
  overflow-x: hidden;
}

.app-container {
  min-height: 100vh;
}
</style>
3. pages/index.vue
vue
<template>
  <div>
    <!-- Dashboard content is handled by AdminLayout -->
  </div>
</template>

<script setup>
definePageMeta({
  layout: 'default'
})
</script>
4. components/admin/AdminLayout.vue
vue
<template>
  <div class="admin-layout">
    <ToastMessage />
    <Sidebar :activeSection="currentSection" @navigate="handleNavigate" />
    <div class="main-content">
      <Topbar :pageTitle="currentTitle" @toggle-sidebar="toggleSidebar" />
      <div class="content-area">
        <Dashboard v-if="currentSection === 'dashboard'" />
        <OrdersView v-if="currentSection === 'orders'" />
        <ProductsView v-if="currentSection === 'products'" />
        <CustomersView v-if="currentSection === 'customers'" />
        <AnalyticsView v-if="currentSection === 'analytics'" />
        <SettingsView v-if="currentSection === 'settings'" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, provide } from 'vue'
import { useToastStore } from '~/stores/toast'
import Sidebar from './Sidebar.vue'
import Topbar from './Topbar.vue'
import ToastMessage from '~/components/ui/ToastMessage.vue'
import Dashboard from './Dashboard.vue'
import OrdersView from './OrdersView.vue'
import ProductsView from './ProductsView.vue'
import CustomersView from './CustomersView.vue'
import AnalyticsView from './AnalyticsView.vue'
import SettingsView from './SettingsView.vue'

const currentSection = ref('dashboard')
const toastStore = useToastStore()

const titles = {
  dashboard: 'Dashboard Overview',
  orders: 'Orders Management',
  products: 'Products Catalogue',
  customers: 'Customers Database',
  analytics: 'Analytics Insights',
  settings: 'Store Settings'
}

const currentTitle = computed(() => titles[currentSection.value] || currentSection.value)

const handleNavigate = (section) => {
  currentSection.value = section
}

const toggleSidebar = () => {
  const sidebar = document.getElementById('sidebar')
  if (sidebar) sidebar.classList.toggle('open')
}

provide('showToast', (message, type = 'success') => {
  toastStore.showToast(message, type)
})
</script>

<style scoped>
.admin-layout {
  display: flex;
  min-height: 100vh;
}

.main-content {
  flex: 1;
  margin-left: var(--sidebar);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.content-area {
  flex: 1;
  padding: 28px 32px;
  overflow-y: auto;
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 768px) {
  .main-content {
    margin-left: 0;
  }
  .content-area {
    padding: 18px 16px;
  }
}
</style>
5. components/admin/Sidebar.vue
vue
<template>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-logo">
      <div class="logo-mark">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
          <polyline points="9 22 9 12 15 12 15 22"/>
        </svg>
      </div>
      <div>
        <div class="logo-text">Supro<span>.</span></div>
        <div class="logo-badge">Admin Panel</div>
      </div>
    </div>

    <div class="sidebar-nav">
      <div class="nav-section">Main</div>
      <button
        v-for="item in mainNav"
        :key="item.id"
        class="nav-item"
        :class="{ active: activeSection === item.id }"
        @click="navigate(item.id)"
      >
        <component :is="item.icon" class="nav-icon" />
        <span>{{ item.label }}</span>
        <span v-if="item.badge" class="badge" :class="item.badgeClass">{{ item.badge }}</span>
      </button>

      <div class="nav-section">Store</div>
      <button
        v-for="item in storeNav"
        :key="item.id"
        class="nav-item"
        :class="{ active: activeSection === item.id }"
        @click="navigate(item.id)"
      >
        <component :is="item.icon" class="nav-icon" />
        <span>{{ item.label }}</span>
      </button>

      <div class="nav-section">System</div>
      <button
        v-for="item in systemNav"
        :key="item.id"
        class="nav-item"
        :class="{ active: activeSection === item.id }"
        @click="navigate(item.id)"
      >
        <component :is="item.icon" class="nav-icon" />
        <span>{{ item.label }}</span>
      </button>
    </div>

    <div class="sidebar-footer">
      <div class="admin-info">
        <div class="admin-avatar">SA</div>
        <div class="admin-details">
          <div class="admin-name">Super Admin</div>
          <div class="admin-role">Administrator</div>
        </div>
        <button class="admin-menu" @click="showProfileMenu">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="1"/>
            <circle cx="19" cy="12" r="1"/>
            <circle cx="5" cy="12" r="1"/>
          </svg>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { IconDashboard, IconPackage, IconUsers, IconTrendingUp, IconSettings, IconShoppingBag, IconTag } from '~/components/icons'

const props = defineProps({
  activeSection: {
    type: String,
    default: 'dashboard'
  }
})

const emit = defineEmits(['navigate'])

const showToast = inject('showToast')

const mainNav = [
  { id: 'dashboard', label: 'Dashboard', icon: IconDashboard },
  { id: 'orders', label: 'Orders', icon: IconShoppingBag, badge: '12', badgeClass: 'badge-warning' },
  { id: 'products', label: 'Products', icon: IconPackage },
  { id: 'customers', label: 'Customers', icon: IconUsers }
]

const storeNav = [
  { id: 'analytics', label: 'Analytics', icon: IconTrendingUp }
]

const systemNav = [
  { id: 'settings', label: 'Settings', icon: IconSettings }
]

const navigate = (id) => {
  emit('navigate', id)
}

const showProfileMenu = () => {
  showToast('Profile options coming soon', 'info')
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar);
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  z-index: 100;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.sidebar-logo {
  padding: 24px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-mark {
  width: 40px;
  height: 40px;
  background: var(--accent);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.logo-text {
  font-family: var(--serif);
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.5px;
}

.logo-text span {
  color: var(--accent2);
}

.logo-badge {
  font-size: 0.6rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text3);
  margin-top: -4px;
}

.sidebar-nav {
  flex: 1;
  padding: 20px 0;
  overflow-y: auto;
}

.nav-section {
  padding: 16px 20px 8px;
  font-size: 0.7rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text3);
  font-weight: 500;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: calc(100% - 16px);
  margin: 4px 8px;
  padding: 10px 12px;
  background: none;
  border: none;
  border-radius: 8px;
  color: var(--text2);
  font-family: var(--sans);
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: left;
  position: relative;
}

.nav-item:hover {
  background: var(--surface2);
  color: var(--text);
}

.nav-item.active {
  background: var(--accentglow);
  color: var(--accent2);
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: var(--accent);
  border-radius: 3px;
}

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.badge {
  margin-left: auto;
  font-size: 0.65rem;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 600;
}

.badge-warning {
  background: rgba(245, 156, 42, 0.15);
  color: var(--accent2);
}

.sidebar-footer {
  padding: 16px;
  border-top: 1px solid var(--border);
}

.admin-info {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
  background: var(--surface2);
  border-radius: 10px;
}

.admin-avatar {
  width: 40px;
  height: 40px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  color: white;
}

.admin-details {
  flex: 1;
}

.admin-name {
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text);
}

.admin-role {
  font-size: 0.7rem;
  color: var(--text3);
}

.admin-menu {
  background: none;
  border: none;
  color: var(--text3);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: all 0.2s;
}

.admin-menu:hover {
  color: var(--accent2);
  background: var(--surface3);
}

@media (max-width: 768px) {
  .sidebar {
    transform: translateX(-100%);
  }
  .sidebar.open {
    transform: translateX(0);
  }
}
</style>
6. components/admin/Topbar.vue
vue
<template>
  <header class="topbar">
    <div class="topbar-left">
      <button class="mobile-menu-btn" @click="$emit('toggle-sidebar')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>
      <div class="page-header">
        <h1 class="page-title" v-html="pageTitle"></h1>
        <div class="breadcrumb">
          <span>Home</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
          <span class="current">{{ currentBreadcrumb }}</span>
        </div>
      </div>
    </div>

    <div class="topbar-right">
      <div class="search-bar">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <circle cx="11" cy="11" r="8"/>
          <line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input type="text" placeholder="Search..." v-model="searchQuery" @keyup.enter="handleSearch"/>
      </div>

      <button class="icon-btn" @click="showNotifications">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
        <span class="notification-dot"></span>
      </button>

      <button class="icon-btn" @click="showMessages">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </button>

      <button class="user-menu" @click="toggleUserMenu">
        <div class="user-avatar">SA</div>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>
    </div>

    <div v-if="userMenuOpen" class="user-dropdown">
      <a href="#" @click.prevent="handleProfile">Profile Settings</a>
      <a href="#" @click.prevent="handleLogout">Logout</a>
    </div>
  </header>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  pageTitle: {
    type: String,
    default: 'Dashboard'
  }
})

defineEmits(['toggle-sidebar'])

const showToast = inject('showToast')
const searchQuery = ref('')
const userMenuOpen = ref(false)

const currentBreadcrumb = computed(() => {
  return props.pageTitle.split(' ')[0]
})

const handleSearch = () => {
  if (searchQuery.value.trim()) {
    showToast(`Searching for: ${searchQuery.value}`, 'info')
    searchQuery.value = ''
  }
}

const showNotifications = () => {
  showToast('No new notifications', 'info')
}

const showMessages = () => {
  showToast('No new messages', 'info')
}

const toggleUserMenu = () => {
  userMenuOpen.value = !userMenuOpen.value
  setTimeout(() => {
    userMenuOpen.value = false
  }, 3000)
}

const handleProfile = () => {
  showToast('Profile settings coming soon', 'info')
  userMenuOpen.value = false
}

const handleLogout = () => {
  showToast('Logout feature coming soon', 'info')
  userMenuOpen.value = false
}
</script>

<style scoped>
.topbar {
  height: 70px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 32px;
  position: sticky;
  top: 0;
  z-index: 50;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.mobile-menu-btn {
  display: none;
  background: none;
  border: none;
  color: var(--text2);
  cursor: pointer;
  padding: 8px;
  border-radius: 6px;
  transition: all 0.2s;
}

.mobile-menu-btn:hover {
  background: var(--surface2);
  color: var(--accent2);
}

.page-header h1 {
  font-family: var(--serif);
  font-size: 1.4rem;
  font-weight: 400;
  color: var(--text);
  margin-bottom: 4px;
}

.page-header :deep(span) {
  color: var(--accent2);
  font-style: italic;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.7rem;
  color: var(--text3);
}

.breadcrumb .current {
  color: var(--accent2);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--surface2);
  border: 1px solid var(--border2);
  padding: 0 14px;
  height: 40px;
  border-radius: 8px;
  width: 260px;
  transition: all 0.2s;
}

.search-bar:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accentglow);
}

.search-bar input {
  background: none;
  border: none;
  outline: none;
  font-size: 0.85rem;
  color: var(--text);
  width: 100%;
}

.search-bar input::placeholder {
  color: var(--text3);
}

.icon-btn {
  width: 40px;
  height: 40px;
  background: none;
  border: 1px solid var(--border2);
  border-radius: 8px;
  color: var(--text2);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  position: relative;
}

.icon-btn:hover {
  border-color: var(--accent);
  color: var(--accent2);
  background: var(--surface2);
}

.notification-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 8px;
  height: 8px;
  background: var(--accent);
  border-radius: 50%;
  border: 2px solid var(--surface);
}

.user-menu {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: 1px solid var(--border2);
  border-radius: 8px;
  padding: 4px 8px 4px 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.user-menu:hover {
  border-color: var(--accent);
  background: var(--surface2);
}

.user-avatar {
  width: 32px;
  height: 32px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 600;
  color: white;
}

.user-dropdown {
  position: absolute;
  top: 60px;
  right: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 0;
  min-width: 180px;
  z-index: 100;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.user-dropdown a {
  display: block;
  padding: 8px 16px;
  color: var(--text2);
  text-decoration: none;
  font-size: 0.85rem;
  transition: all 0.2s;
}

.user-dropdown a:hover {
  background: var(--surface2);
  color: var(--accent2);
}

@media (max-width: 768px) {
  .topbar {
    padding: 0 16px;
  }
  .mobile-menu-btn {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .search-bar {
    display: none;
  }
  .page-header h1 {
    font-size: 1.2rem;
  }
}
</style>
7. components/admin/Dashboard.vue
vue
<template>
  <div class="dashboard">
    <StatsCards />
    
    <div class="dashboard-grid">
      <RevenueChart />
      <TrafficSources />
    </div>
    
    <div class="dashboard-grid">
      <RecentOrders />
      <TopProducts />
    </div>
    
    <ActivityFeed />
  </div>
</template>

<script setup>
import StatsCards from './StatsCards.vue'
import RevenueChart from './RevenueChart.vue'
import TrafficSources from './TrafficSources.vue'
import RecentOrders from './RecentOrders.vue'
import TopProducts from './TopProducts.vue'
import ActivityFeed from './ActivityFeed.vue'
</script>

<style scoped>
.dashboard {
  animation: fadeIn 0.4s ease;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 24px;
  margin-bottom: 24px;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1024px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}
</style>
8. components/admin/StatsCards.vue
vue
<template>
  <div class="stats-grid">
    <div v-for="stat in stats" :key="stat.label" class="stat-card" :class="stat.colorClass">
      <div class="stat-header">
        <span class="stat-label">{{ stat.label }}</span>
        <component :is="stat.icon" class="stat-icon" />
      </div>
      <div class="stat-value">{{ stat.value }}</div>
      <div class="stat-change" :class="stat.trend">
        {{ stat.changeIcon }} {{ stat.changePercent }}%
        <span class="stat-period">{{ stat.changePeriod }}</span>
      </div>
      <div class="stat-subtitle">{{ stat.subtitle }}</div>
      <svg class="sparkline" :viewBox="stat.sparkline.viewBox">
        <polyline :points="stat.sparkline.points" fill="none" :stroke="stat.sparkline.stroke" stroke-width="2"/>
      </svg>
    </div>
  </div>
</template>

<script setup>
import { IconDollarSign, IconShoppingBag, IconUsers, IconTrendingUp } from '~/components/icons'

const stats = [
  {
    label: 'Total Revenue',
    value: '£84,291',
    changePercent: '12.4',
    changePeriod: 'vs last month',
    trend: 'up',
    changeIcon: '↑',
    subtitle: '£9,834 this week',
    icon: IconDollarSign,
    colorClass: 'c1',
    sparkline: {
      viewBox: '0 0 80 40',
      points: '0,35 15,28 25,30 40,15 55,18 65,8 80,10',
      stroke: 'var(--accent)'
    }
  },
  {
    label: 'Total Orders',
    value: '1,847',
    changePercent: '8.1',
    changePeriod: 'vs last month',
    trend: 'up',
    changeIcon: '↑',
    subtitle: '42 orders today',
    icon: IconShoppingBag,
    colorClass: 'c2',
    sparkline: {
      viewBox: '0 0 80 40',
      points: '0,32 12,25 25,28 38,18 50,20 65,12 80,8',
      stroke: 'var(--green)'
    }
  },
  {
    label: 'Total Customers',
    value: '5,392',
    changePercent: '3.6',
    changePeriod: 'vs last month',
    trend: 'up',
    changeIcon: '↑',
    subtitle: '148 new this month',
    icon: IconUsers,
    colorClass: 'c3',
    sparkline: {
      viewBox: '0 0 80 40',
      points: '0,30 20,22 35,24 50,16 65,14 80,10',
      stroke: 'var(--blue)'
    }
  },
  {
    label: 'Avg Order Value',
    value: '£45.60',
    changePercent: '1.2',
    changePeriod: 'vs last month',
    trend: 'down',
    changeIcon: '↓',
    subtitle: 'Target: £50.00',
    icon: IconTrendingUp,
    colorClass: 'c4',
    sparkline: {
      viewBox: '0 0 80 40',
      points: '0,18 15,22 30,16 45,20 60,14 80,18',
      stroke: 'var(--yellow)'
    }
  }
]
</script>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  margin-bottom: 28px;
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  position: relative;
  overflow: hidden;
  transition: all 0.3s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  border-color: var(--border2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
}

.stat-card.c1::before { background: var(--accent); }
.stat-card.c2::before { background: var(--green); }
.stat-card.c3::before { background: var(--blue); }
.stat-card.c4::before { background: var(--yellow); }

.stat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.stat-label {
  font-size: 0.7rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text3);
  font-weight: 500;
}

.stat-icon {
  width: 20px;
  height: 20px;
  opacity: 0.6;
}

.stat-value {
  font-family: var(--serif);
  font-size: 2rem;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 8px;
}

.stat-change {
  font-size: 0.75rem;
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 4px;
}

.stat-change.up {
  color: var(--green);
}

.stat-change.down {
  color: var(--red);
}

.stat-period {
  color: var(--text3);
  font-weight: normal;
}

.stat-subtitle {
  font-size: 0.7rem;
  color: var(--text3);
}

.sparkline {
  position: absolute;
  bottom: 12px;
  right: 12px;
  width: 80px;
  height: 40px;
  opacity: 0.15;
}

@media (max-width: 1024px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
9. components/admin/RevenueChart.vue
vue
<template>
  <div class="card">
    <div class="card-header">
      <div>
        <h3 class="card-title">Revenue Overview</h3>
        <p class="card-subtitle">Last 8 months performance</p>
      </div>
      <button class="card-action" @click="exportChart">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        Export
      </button>
    </div>
    <div class="chart-container" ref="chartContainer"></div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useDashboardStore } from '~/stores/dashboard'

const showToast = inject('showToast')
const dashboardStore = useDashboardStore()
const chartContainer = ref(null)

const months = ['Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr']
const revenues = [62, 70, 88, 105, 78, 90, 96, 84]

const exportChart = () => {
  showToast('Exporting chart data...', 'success')
}

onMounted(() => {
  if (chartContainer.value) {
    const max = Math.max(...revenues)
    chartContainer.value.innerHTML = revenues.map((value, index) => `
      <div class="bar-wrapper">
        <div class="bar" style="height: ${(value / max) * 100}%">
          <div class="bar-tooltip">£${value}k</div>
        </div>
        <div class="bar-label">${months[index]}</div>
      </div>
    `).join('')
  }
})
</script>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}

.card-title {
  font-family: var(--serif);
  font-size: 1.1rem;
  font-weight: 400;
  color: var(--text);
  margin-bottom: 4px;
}

.card-subtitle {
  font-size: 0.75rem;
  color: var(--text3);
}

.card-action {
  background: none;
  border: 1px solid var(--border2);
  border-radius: 6px;
  padding: 6px 12px;
  color: var(--text2);
  font-size: 0.75rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s;
}

.card-action:hover {
  border-color: var(--accent);
  color: var(--accent2);
}

.chart-container {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  height: 200px;
  padding: 0 0 30px 0;
  position: relative;
}

.chart-container::after {
  content: '';
  position: absolute;
  bottom: 30px;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--border);
}

.bar-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  height: 100%;
}

.bar {
  width: 100%;
  background: linear-gradient(180deg, var(--accent), var(--accent3));
  border-radius: 4px 4px 0 0;
  margin-top: auto;
  position: relative;
  cursor: pointer;
  transition: all 0.3s ease;
  min-height: 4px;
}

.bar:hover {
  background: linear-gradient(180deg, var(--accent2), var(--accent));
  transform: scaleX(1.05);
}

.bar-tooltip {
  position: absolute;
  top: -28px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--surface3);
  color: var(--accent2);
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 0.7rem;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
}

.bar:hover .bar-tooltip {
  opacity: 1;
}

.bar-label {
  font-size: 0.7rem;
  color: var(--text3);
}
</style>
10. components/admin/RecentOrders.vue
vue
<template>
  <div class="card">
    <div class="card-header">
      <div>
        <h3 class="card-title">Recent Orders</h3>
        <p class="card-subtitle">Latest customer transactions</p>
      </div>
      <button class="card-action" @click="viewAllOrders">
        View All
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
    </div>
    <div class="table-responsive">
      <table class="orders-table">
        <thead>
          <tr>
            <th>Order ID</th>
            <th>Customer</th>
            <th>Date</th>
            <th>Status</th>
            <th>Items</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in recentOrders" :key="order.id">
            <td class="order-id">{{ order.id }}</td>
            <td>
              <div class="customer-info">
                <div class="customer-avatar" :style="{ background: order.avatarBg }">
                  {{ order.avatar }}
                </div>
                <span>{{ order.customer }}</span>
              </div>
            </td>
            <td>{{ order.date }}</td>
            <td>
              <span class="status-badge" :class="getStatusClass(order.status)">
                {{ getStatusIcon(order.status) }} {{ order.status }}
              </span>
            </td>
            <td>{{ order.items }}</td>
            <td class="amount">{{ order.amount }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useOrderStore } from '~/stores/order'

const showToast = inject('showToast')
const orderStore = useOrderStore()

const recentOrders = computed(() => orderStore.recentOrders)

const getStatusClass = (status) => {
  const classes = {
    delivered: 'status-delivered',
    shipped: 'status-shipped',
    processing: 'status-processing',
    cancelled: 'status-cancelled',
    pending: 'status-pending'
  }
  return classes[status] || 'status-pending'
}

const getStatusIcon = (status) => {
  const icons = {
    delivered: '✓',
    shipped: '→',
    processing: '◉',
    cancelled: '✗',
    pending: '○'
  }
  return icons[status] || '○'
}

const viewAllOrders = () => {
  const ordersBtn = document.querySelector('[data-nav="orders"]')
  if (ordersBtn) ordersBtn.click()
}
</script>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.card-title {
  font-family: var(--serif);
  font-size: 1.1rem;
  font-weight: 400;
  color: var(--text);
  margin-bottom: 4px;
}

.card-subtitle {
  font-size: 0.75rem;
  color: var(--text3);
}

.card-action {
  background: none;
  border: none;
  color: var(--accent2);
  font-size: 0.8rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: all 0.2s;
}

.card-action:hover {
  color: var(--accent);
  gap: 8px;
}

.table-responsive {
  overflow-x: auto;
}

.orders-table {
  width: 100%;
  border-collapse: collapse;
}

.orders-table th {
  text-align: left;
  padding: 12px 8px 12px 0;
  color: var(--text3);
  font-size: 0.7rem;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  border-bottom: 1px solid var(--border);
}

.orders-table td {
  padding: 14px 8px 14px 0;
  color: var(--text2);
  font-size: 0.85rem;
  border-bottom: 1px solid var(--border);
}

.orders-table tr:last-child td {
  border-bottom: none;
}

.order-id {
  color: var(--accent2);
  font-weight: 500;
}

.customer-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.customer-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 600;
  color: white;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 0.7rem;
  font-weight: 500;
  text-transform: capitalize;
}

.status-delivered {
  background: rgba(46, 184, 114, 0.12);
  color: #2eb872;
}

.status-shipped {
  background: rgba(58, 143, 212, 0.12);
  color: #3a8fd4;
}

.status-processing {
  background: rgba(245, 156, 42, 0.12);
  color: var(--accent2);
}

.status-cancelled {
  background: rgba(224, 68, 68, 0.12);
  color: #e04444;
}

.status-pending {
  background: rgba(138, 112, 96, 0.12);
  color: var(--text3);
}

.amount {
  color: var(--text);
  font-weight: 500;
}
</style>
11. stores/toast.ts
typescript
import { defineStore } from 'pinia'

interface ToastState {
  message: string
  type: 'success' | 'error' | 'info' | 'warning'
  visible: boolean
  timeout: number | null
}

export const useToastStore = defineStore('toast', {
  state: (): ToastState => ({
    message: '',
    type: 'success',
    visible: false,
    timeout: null
  }),
  
  actions: {
    showToast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
      if (this.timeout) clearTimeout(this.timeout)
      
      this.message = message
      this.type = type
      this.visible = true
      
      this.timeout = setTimeout(() => {
        this.visible = false
      }, 3000)
    },
    
    hideToast() {
      this.visible = false
      if (this.timeout) clearTimeout(this.timeout)
    }
  }
})
12. stores/dashboard.ts
typescript
import { defineStore } from 'pinia'

interface DashboardStats {
  revenue: number
  orders: number
  customers: number
  avgOrderValue: number
}

export const useDashboardStore = defineStore('dashboard', {
  state: () => ({
    stats: {
      revenue: 84291,
      orders: 1847,
      customers: 5392,
      avgOrderValue: 45.6
    } as DashboardStats,
    revenueData: [62, 70, 88, 105, 78, 90, 96, 84],
    months: ['Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr']
  }),
  
  getters: {
    formattedRevenue: (state) => `£${state.stats.revenue.toLocaleString()}`,
    revenueGrowth: () => 12.4,
    orderGrowth: () => 8.1
  },
  
  actions: {
    async fetchDashboardData() {
      // API call would go here
      // For now, using static data
    }
  }
})
13. stores/order.ts
typescript
import { defineStore } from 'pinia'

export interface Order {
  id: string
  customer: string
  avatar: string
  avatarBg: string
  date: string
  status: 'delivered' | 'shipped' | 'processing' | 'cancelled' | 'pending'
  amount: string
  items: number
}

export const useOrderStore = defineStore('order', {
  state: () => ({
    orders: [] as Order[],
    loading: false
  }),
  
  getters: {
    recentOrders: (state) => state.orders.slice(0, 5),
    totalOrders: (state) => state.orders.length,
    totalRevenue: (state) => {
      return state.orders.reduce((sum, order) => {
        const amount = parseFloat(order.amount.replace('£', ''))
        return sum + amount
      }, 0)
    }
  },
  
  actions: {
    async fetchOrders() {
      this.loading = true
      // Mock data
      this.orders = [
        { id: '#SUP-4821', customer: 'Emily Clarke', avatar: 'EC', avatarBg: '#c87030', date: '2 May 2025', status: 'delivered', amount: '£142.00', items: 3 },
        { id: '#SUP-4820', customer: 'James Wright', avatar: 'JW', avatarBg: '#3a8fd4', date: '2 May 2025', status: 'shipped', amount: '£89.90', items: 1 },
        { id: '#SUP-4819', customer: 'Aisha Rahman', avatar: 'AR', avatarBg: '#2eb872', date: '1 May 2025', status: 'processing', amount: '£237.50', items: 4 },
        { id: '#SUP-4818', customer: 'Tom Nguyen', avatar: 'TN', avatarBg: '#8030c0', date: '1 May 2025', status: 'delivered', amount: '£56.90', items: 1 },
        { id: '#SUP-4817', customer: 'Sara Johansson', avatar: 'SJ', avatarBg: '#e04444', date: '30 Apr 2025', status: 'cancelled', amount: '£178.00', items: 2 },
        { id: '#SUP-4816', customer: 'Luca Ferrari', avatar: 'LF', avatarBg: '#c87030', date: '30 Apr 2025', status: 'delivered', amount: '£95.90', items: 2 }
      ]
      this.loading = false
    }
  }
})
14. components/ui/ToastMessage.vue
vue
<template>
  <Transition name="toast">
    <div v-if="visible" class="toast" :class="`toast-${type}`">
      <div class="toast-icon">
        <svg v-if="type === 'success'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
        <svg v-else-if="type === 'error'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="12" x2="12" y2="16"/>
          <line x1="12" y1="8" x2="12.01" y2="8"/>
        </svg>
      </div>
      <span class="toast-message">{{ message }}</span>
      <button class="toast-close" @click="hideToast">×</button>
    </div>
  </Transition>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { useToastStore } from '~/stores/toast'

const toastStore = useToastStore()
const { message, type, visible } = storeToRefs(toastStore)
const { hideToast } = toastStore
</script>

<style scoped>
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: var(--surface);
  border-radius: 10px;
  border-left: 4px solid;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  z-index: 1000;
  min-width: 280px;
  animation: slideIn 0.3s ease;
}

.toast-success {
  border-left-color: var(--green);
}
.toast-success .toast-icon {
  color: var(--green);
}

.toast-error {
  border-left-color: var(--red);
}
.toast-error .toast-icon {
  color: var(--red);
}

.toast-info, .toast-warning {
  border-left-color: var(--accent2);
}
.toast-info .toast-icon, .toast-warning .toast-icon {
  color: var(--accent2);
}

.toast-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.toast-message {
  flex: 1;
  color: var(--text);
  font-size: 0.85rem;
}

.toast-close {
  background: none;
  border: none;
  color: var(--text3);
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0 4px;
  transition: all 0.2s;
}

.toast-close:hover {
  color: var(--accent2);
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(100%);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(100%);
}
</style>
15. components/icons/index.ts
typescript
// Icon components as simple SVG templates
export const IconDashboard = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <rect x="3" y="3" width="7" height="7"/>
    <rect x="14" y="3" width="7" height="7"/>
    <rect x="14" y="14" width="7" height="7"/>
    <rect x="3" y="14" width="7" height="7"/>
  </svg>`
}

export const IconPackage = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.57a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.57a2 2 0 0 0-1.34-2.23z"/>
  </svg>`
}

export const IconUsers = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>`
}

export const IconTrendingUp = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>
    <polyline points="17 6 23 6 23 12"/>
  </svg>`
}

export const IconSettings = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33 1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82 1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>`
}

export const IconShoppingBag = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/>
    <line x1="3" y1="6" x2="21" y2="6"/>
    <path d="M16 10a4 4 0 0 1-8 0"/>
  </svg>`
}

export const IconTag = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
    <line x1="7" y1="7" x2="7.01" y2="7"/>
  </svg>`
}

export const IconDollarSign = {
  template: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <line x1="12" y1="1" x2="12" y2="23"/>
    <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
  </svg>`
}
To use these icon components in Vue templates, you need to create wrapper Vue components. For example:

vue
<!-- components/icons/IconDashboard.vue -->
<template>
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <rect x="3" y="3" width="7" height="7"/>
    <rect x="14" y="3" width="7" height="7"/>
    <rect x="14" y="14" width="7" height="7"/>
    <rect x="3" y="14" width="7" height="7"/>
  </svg>
</template>

'''
})