/*
 * SidebarNav.jsx — Floating Modern Vertical Rail Navigation.
 *
 * Implements the sleek Dribbble/Ekonomi mini rail design:
 *   - Circular brand badge with spacecraft logo
 *   - Quick icon-based navigation between all console views
 *   - Active glow pills and tooltips
 *   - Dedicated Theme Toggle (Dark / Light mode)
 *   - Operator status indicator
 */

import React from "react";
import Icon from "./ui/Icon";
import { useTheme } from "../state/ThemeContext";

export const SIDEBAR_ITEMS = [
  { id: "overview", label: "Mission Overview", icon: "grid" },
  { id: "pipeline", label: "Pipeline Demo", icon: "rocket" },
  { id: "telemetry", label: "Telemetry", icon: "activity" },
  { id: "investigation", label: "Fault Investigation", icon: "shield" },
  { id: "reconciliation", label: "Reconciliation", icon: "nodes" },
  { id: "physics", label: "Physics / State", icon: "orbit" },
  { id: "recovery", label: "Recovery Plans", icon: "tools" },
  { id: "evidence", label: "ECSS Evidence", icon: "book" },
  { id: "audit", label: "Audit Records", icon: "fingerprint" },
  { id: "evaluation", label: "Evaluation", icon: "chart" },
];

export default function SidebarNav({ activeTab, onSelectTab }) {
  const { theme, toggleTheme } = useTheme();

  return (
    <aside className="sidebar-nav" aria-label="Sidebar console navigation">
      <div className="sidebar-nav__top">
        {/* Brand Mark */}
        <div className="sidebar-logo" title="SENTINEL Mission Operations Console">
          <div className="sidebar-logo__circle">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
        </div>

        {/* Primary View Icons */}
        <nav className="sidebar-menu" role="tablist" aria-orientation="vertical">
          {SIDEBAR_ITEMS.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-label={item.label}
                title={item.label}
                className={`sidebar-btn ${isActive ? "sidebar-btn--active" : ""}`}
                onClick={() => onSelectTab(item.id)}
              >
                <Icon name={item.icon} size={18} />
                <span className="sidebar-btn__tooltip">{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Controls: Theme Toggle & Operator Profile */}
      <div className="sidebar-nav__bottom">
        {/* Theme Toggle Button */}
        <button
          type="button"
          className="sidebar-theme-toggle"
          onClick={toggleTheme}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        >
          <div className="theme-toggle-disc">
            <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
          </div>
        </button>

        {/* Operator Profile */}
        <div className="sidebar-avatar" title="Flight Operations Director (Active)">
          <div className="avatar-img-wrap">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.8" />
              <path d="M4 20c0-4 4-6 8-6s8 2 8 6" stroke="currentColor" strokeWidth="1.8" />
            </svg>
            <span className="avatar-status-dot" aria-hidden="true" />
          </div>
          <span className="sidebar-btn__tooltip">Flight Director (Online)</span>
        </div>
      </div>
    </aside>
  );
}
