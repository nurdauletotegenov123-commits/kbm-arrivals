import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Menu, X, Bell, FilePlus2, CheckSquare, FileText, LogOut, LayoutDashboard, ClipboardList, ChartColumn, Check,
} from 'lucide-react';
import { Permission } from '../types';
import { LanguageSwitcher } from './LanguageSwitcher';
import { toast } from './Toasts';

// Время уведомления: сегодня — только время, иначе дата + время
const formatNotifTime = (iso: string): string => {
  const d = new Date(iso);
  const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${d.toLocaleDateString('ru-RU')} ${time}`;
};

/** Шапка портала: те же пункты меню, что на остальных страницах сайта (приходят с /api/v1/session-token/). */
export interface PortalInfo {
  nav: { url: string; label: string; active: boolean }[];
  home: string;
  profile: string;
  logout: string;
  csrf: string;
  user: string;
  role_label: string;
}

const PERMIT_ROLE_LABELS: Record<string, string> = {
  WORK_PRODUCER: 'Производитель работ',
  ISSUER: 'Выдающий наряд',
  ADMITTING: 'Допускающий',
  RESPONSIBLE: 'Ответственный руководитель',
  COORDINATOR: 'Согласующий',
  AUDITOR: 'Аудитор',
  ADMIN: 'Администратор нарядов',
};

interface LayoutProps {
  children: React.ReactNode;
  onNavigate: () => void;
  onNavigateLoto?: () => void;
  onNavigateMyTasks?: () => void;
  onNavigateArchive?: () => void;
  onNavigateAuditStats?: () => void;
  onCreate?: () => void;
  onLogout: () => void;
  onSelectPermit?: (id: string) => void;
  portal?: PortalInfo | null;
  user?: {
    name: string;
    position: string;
    department: string;
    organization: string;
    role?: string;
    permissions: Permission[];
  };
  currentView?: string;
}

export const Layout: React.FC<LayoutProps> = ({
  children, onNavigate, onNavigateLoto, onNavigateMyTasks, onNavigateArchive, onNavigateAuditStats,
  onCreate, onLogout, onSelectPermit, portal, user, currentView,
}) => {
  const { t } = useTranslation();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [isNotifOpen, setIsNotifOpen] = useState(false);

  const notifRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (notifRef.current && !notifRef.current.contains(target)) setIsNotifOpen(false);
      if (isSidebarOpen && sidebarRef.current && !sidebarRef.current.contains(target)
          && menuButtonRef.current && !menuButtonRef.current.contains(target)) {
        setIsSidebarOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setIsNotifOpen(false); setIsSidebarOpen(false); } };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', handleClickOutside); document.removeEventListener('keydown', onKey); };
  }, [isSidebarOpen]);

  const hasPermission = (permission: Permission) => user?.permissions?.includes(permission);
  const userRole = user?.role || '';
  const canCreatePermit = userRole === 'ISSUER' || userRole === 'ADMITTING' || userRole === 'ADMIN';
  const canViewStatistics = userRole === 'AUDITOR' || userRole === 'ADMIN';

  // «Живые» уведомления: опрос раз в 30 с + сразу при возврате на вкладку + всплывающая плашка о новом
  const knownIdsRef = useRef<Set<number>>(new Set());
  const firstLoadRef = useRef(true);
  const fetchNotifs = () => {
    if (document.hidden) return;
    const token = localStorage.getItem('auth_token');
    if (!token) return;
    fetch('/api/v1/notifications/', { headers: { 'Authorization': `Token ${token}` } })
      .then(r => (r.ok ? r.json() : []))
      .then(data => {
        const list: any[] = Array.isArray(data) ? data : (data?.results || []);
        const items = list.filter((n: any) => !n.is_read);
        if (firstLoadRef.current) firstLoadRef.current = false;
        else {
          const fresh = items.filter(n => !knownIdsRef.current.has(n.id));
          if (fresh.length > 0) toast({ message: fresh[0].title || 'Новое уведомление', type: 'info' });
        }
        items.forEach((n: any) => knownIdsRef.current.add(n.id));
        setNotifications(items);
      })
      .catch(e => console.error('Ошибка уведомлений:', e));
  };
  useEffect(() => {
    fetchNotifs();
    const interval = setInterval(fetchNotifs, 30000);
    const onVisible = () => { if (!document.hidden) fetchNotifs(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { clearInterval(interval); document.removeEventListener('visibilitychange', onVisible); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleMarkAsRead = async (id: number) => {
    try {
      const token = localStorage.getItem('auth_token');
      await fetch(`/api/v1/notifications/${id}/mark_read/`, {
        method: 'POST',
        headers: { 'Authorization': `Token ${token}`, 'Content-Type': 'application/json' },
      });
      setNotifications(prev => prev.filter(n => n.id !== id));
    } catch (e) { console.error(e); }
  };

  const go = (fn?: () => void) => () => { fn?.(); setIsSidebarOpen(false); };
  const initial = (portal?.user || user?.name || '?').trim().charAt(0).toUpperCase();

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col text-slate-700">
      <style>{`
        @keyframes pop { from { opacity: 0; transform: translateY(-4px) scale(.98); } to { opacity: 1; transform: none; } }
        .pop { animation: pop .16s cubic-bezier(.23, 1, .32, 1) both; transform-origin: top right; }
        @media (prefers-reduced-motion: reduce) { .pop { animation: none; } }
      `}</style>

      {/* ===== Шапка портала (как на остальных страницах сайта) ===== */}
      <header className="sticky top-0 z-[60] bg-kbm-black text-white border-b-[3px] border-kbm-blue shrink-0">
        <div className="flex items-center gap-2 sm:gap-4 px-3 sm:px-5 min-h-[60px]">
          <button
            ref={menuButtonRef}
            onClick={() => setIsSidebarOpen(v => !v)}
            className="lg:hidden -ml-1 p-2 rounded-lg text-white/80 hover:text-white hover:bg-white/10"
            aria-label={isSidebarOpen ? 'Закрыть меню раздела' : 'Меню раздела'}
          >
            {isSidebarOpen ? <X size={22} /> : <Menu size={22} />}
          </button>

          <a href={portal?.home || '/'} className="flex items-center gap-2.5 shrink-0 py-2" aria-label="На главную портала">
            <img src="/static/img/logo-shield.png" alt="" className="h-9 w-auto" />
            <span className="hidden sm:flex flex-col leading-tight">
              <b className="text-[15px] font-bold tracking-wide uppercase">Каражанбасмунай</b>
              <span className="text-[12px] text-[#8fcdee]">Портал сотрудника</span>
            </span>
          </a>

          {/* Пункты меню портала — на широком экране в шапке, на телефоне строкой ниже */}
          <nav className="hidden lg:flex items-stretch self-stretch gap-1 ml-4 flex-wrap">
            {portal?.nav.map(item => (
              <a
                key={item.url}
                href={item.url}
                className={`relative flex items-center px-3 text-[15px] font-semibold transition-colors ${item.active ? 'text-white' : 'text-white/65 hover:text-white'}`}
              >
                {item.label}
                {item.active && <span className="absolute left-3 right-3 -bottom-[3px] h-[3px] bg-white rounded-t" />}
              </a>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1.5 sm:gap-3">
            <LanguageSwitcher className="hidden md:inline-flex" />

            {/* Уведомления */}
            <div className="relative" ref={notifRef}>
              <button
                onClick={() => setIsNotifOpen(v => !v)}
                className={`relative p-2 rounded-full transition-colors ${isNotifOpen ? 'bg-white/15 text-white' : 'text-white/75 hover:text-white hover:bg-white/10'}`}
                aria-label={`Уведомления${notifications.length ? `: ${notifications.length}` : ''}`}
              >
                <Bell size={21} />
                {notifications.length > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 bg-red-600 text-white text-[10px] font-bold h-[18px] min-w-[18px] px-1 rounded-full ring-2 ring-kbm-black flex items-center justify-center tabular-nums">
                    {notifications.length > 99 ? '99+' : notifications.length}
                  </span>
                )}
              </button>
              {isNotifOpen && (
                <div className="absolute right-0 mt-2 w-[380px] max-w-[calc(100vw-24px)] bg-white text-slate-700 border border-slate-200 rounded-2xl shadow-pop z-50 overflow-hidden pop">
                  <div className="px-4 py-3.5 border-b border-slate-100 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-slate-900 text-sm">{t('layout.notifications')}</span>
                      {notifications.length > 0 && (
                        <span className="min-w-[20px] h-5 px-1.5 rounded-full bg-blue-50 text-blue-700 text-[11px] font-bold flex items-center justify-center">
                          {notifications.length > 99 ? '99+' : notifications.length}
                        </span>
                      )}
                    </div>
                    {notifications.length > 0 && (
                      <button
                        onClick={() => notifications.forEach(n => handleMarkAsRead(n.id))}
                        className="flex items-center gap-1 text-xs font-semibold text-slate-500 hover:text-blue-700 hover:bg-blue-50 px-2 py-1 rounded-md transition-colors"
                      >
                        <Check size={13} /> {t('layout.markAllRead')}
                      </button>
                    )}
                  </div>
                  <div className="max-h-[380px] overflow-y-auto divide-y divide-slate-100">
                    {notifications.length === 0 ? (
                      <div className="py-10 flex flex-col items-center gap-3">
                        <div className="w-12 h-12 rounded-full bg-slate-50 border border-slate-100 flex items-center justify-center text-slate-300"><Bell size={22} /></div>
                        <p className="text-sm text-slate-500">{t('layout.noNotifications')}</p>
                      </div>
                    ) : notifications.map(n => (
                      <button
                        key={n.id}
                        onClick={() => { handleMarkAsRead(n.id); if (n.permit_id && onSelectPermit) { onSelectPermit(String(n.permit_id)); setIsNotifOpen(false); } }}
                        className="w-full text-left flex gap-3 px-4 py-3.5 hover:bg-slate-50 transition-colors"
                      >
                        <span className="mt-1.5 w-2 h-2 rounded-full bg-blue-500 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block font-semibold text-sm text-slate-800 leading-snug">{n.title}</span>
                          <span className="block text-xs text-slate-500 mt-1 leading-relaxed">{n.message}</span>
                          <span className="block text-[11px] text-slate-400 mt-1.5 tabular-nums">{formatNotifTime(n.created_at)}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Пользователь — как на портале: клик ведёт в профиль */}
            <a
              href={portal?.profile || '#'}
              className="flex items-center gap-2.5 pl-1 pr-1 sm:pr-2.5 py-1 rounded-full hover:bg-white/10 transition-colors"
              title={`${portal?.user || user?.name || ''} — пароль и профиль`}
            >
              <span className="w-9 h-9 rounded-full bg-kbm-blue text-white font-bold flex items-center justify-center text-[15px]">{initial}</span>
              <span className="hidden xl:flex flex-col leading-tight max-w-[200px]">
                <b className="text-[13.5px] font-semibold truncate">{portal?.user || user?.name}</b>
                <small className="text-[11.5px] text-white/60 truncate">{portal?.role_label || user?.position}</small>
              </span>
            </a>
            <button
              onClick={onLogout}
              className="hidden sm:inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-white/85 hover:text-white border border-white/20 hover:border-white/40 rounded-lg px-3 py-1.5 transition-colors"
            >
              {t('layout.logout')}
            </button>
          </div>
        </div>

        {/* Меню портала на телефоне и планшете */}
        {portal && portal.nav.length > 0 && (
          <nav className="lg:hidden flex gap-1 px-2 pb-1.5 overflow-x-auto border-t border-white/10" aria-label="Разделы портала">
            {portal.nav.map(item => (
              <a key={item.url} href={item.url}
                 className={`shrink-0 px-3 py-2 text-sm font-semibold rounded-md ${item.active ? 'text-white bg-white/10' : 'text-white/65'}`}>
                {item.label}
              </a>
            ))}
          </nav>
        )}
      </header>

      <div className="flex flex-1 w-full relative">
        {/* ===== Панель раздела «Наряд-допуски» ===== */}
        {isSidebarOpen && <div className="lg:hidden fixed inset-0 bg-kbm-black/40 z-30" aria-hidden="true" />}
        <aside
          ref={sidebarRef}
          className={`fixed lg:sticky top-0 lg:top-[63px] left-0 z-40 lg:z-10 h-screen lg:h-[calc(100vh-63px)] w-72 lg:w-64 bg-white border-r border-slate-200
                      transform transition-transform duration-200 ease-out ${isSidebarOpen ? 'translate-x-0 shadow-pop' : '-translate-x-full lg:translate-x-0'}
                      flex flex-col shrink-0`}
          aria-label="Меню раздела «Наряд-допуски»"
        >
          <div className="px-5 pt-5 pb-3">
            <p className="text-[11px] font-bold uppercase tracking-[.12em] text-blue-600">Раздел</p>
            <h2 className="text-lg font-bold text-slate-900 leading-tight mt-0.5">Наряд-допуски</h2>
          </div>

          {canCreatePermit && (
            <div className="px-4 pb-3">
              <button
                onClick={go(onCreate)}
                className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-semibold rounded-xl py-3 shadow-[0_2px_0_#005985] transition-colors"
              >
                <FilePlus2 size={19} /> {t('layout.createPermit')}
              </button>
            </div>
          )}

          <nav className="flex-1 overflow-y-auto px-3 pb-4 space-y-0.5">
            <SidebarLink icon={<LayoutDashboard size={19} />} label={t('layout.main')} active={currentView === 'DASHBOARD'} onClick={go(onNavigate)} />
            <SidebarLink
              icon={<CheckSquare size={19} />}
              label={t('layout.myTasks')}
              badge={notifications.length > 0 ? String(notifications.length) : undefined}
              active={currentView === 'MY_TASKS'}
              onClick={go(onNavigateMyTasks)}
            />
            <SidebarLink icon={<FileText size={19} />} label={t('layout.journal')} active={currentView === 'ARCHIVE'} onClick={go(onNavigateArchive)} />
            {hasPermission('VIEW_LOTO_LOGS') && (
              <SidebarLink icon={<ClipboardList size={19} />} label={t('layout.lotoReports')} active={currentView === 'LOTO_REPORTS'} onClick={go(onNavigateLoto)} />
            )}
            {canViewStatistics && (
              <SidebarLink icon={<ChartColumn size={19} />} label={t('layout.statistics')} active={currentView === 'AUDIT_STATS'} onClick={go(onNavigateAuditStats)} />
            )}
          </nav>

          <div className="border-t border-slate-200 px-5 py-4 text-[12.5px] leading-snug">
            <p className="text-slate-500">Ваша роль в нарядах</p>
            <p className="font-semibold text-slate-800 mt-0.5">{PERMIT_ROLE_LABELS[userRole] || userRole || '—'}</p>
            {user?.position && <p className="text-slate-500 mt-0.5 truncate" title={user.position}>{user.position}</p>}
            <div className="flex items-center justify-between mt-3 sm:hidden">
              <LanguageSwitcher />
              <button onClick={onLogout} className="inline-flex items-center gap-1.5 text-red-600 font-semibold"><LogOut size={15} /> {t('layout.logout')}</button>
            </div>
          </div>
        </aside>

        <main className="flex-1 min-w-0 px-3 sm:px-5 lg:px-7 py-4 lg:py-6 pb-24">
          <div className="w-full max-w-[1400px] mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
};

const SidebarLink: React.FC<{ icon: React.ReactNode; label: string; active?: boolean; badge?: string; onClick?: () => void }> = ({
  icon, label, active, badge, onClick,
}) => (
  <button
    onClick={onClick}
    aria-current={active ? 'page' : undefined}
    className={`w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-[14.5px] font-semibold transition-colors
                ${active ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'}`}
  >
    <span className="flex items-center gap-3">
      <span className={active ? 'text-blue-600' : 'text-slate-400'}>{icon}</span>
      {label}
    </span>
    {badge && <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-red-600 text-white tabular-nums">{badge}</span>}
  </button>
);
