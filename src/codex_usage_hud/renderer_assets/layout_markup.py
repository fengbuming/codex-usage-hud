"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function resizeEdgesMarkup() {
        return `
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-edge-left" data-action="resize" data-edge="left" aria-hidden="true"></div>
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-edge-right" data-action="resize" data-edge="right" aria-hidden="true"></div>
        `;
      }

      function topExpandedResizeMarkup() {
        return `
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-bottom-left" data-action="resize" data-edge="bottom-left" aria-hidden="true"></div>
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-bottom-right" data-action="resize" data-edge="bottom-right" aria-hidden="true"></div>
        `;
      }

      function requestExpandedResizeMarkup() {
        return `
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-top-left" data-action="resize" data-edge="top-left" aria-hidden="true"></div>
          <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-top-right" data-action="resize" data-edge="top-right" aria-hidden="true"></div>
        `;
      }

      function backgroundUsageNotificationMarkup() {
        return `
          <button type="button" class="codex-usage-hud-background-notification"
            data-action="background-usage-open-notification" data-visible="false"
            title="后台用量提醒" aria-label="后台用量提醒" aria-hidden="true" tabindex="-1">
            <span aria-hidden="true">▥</span>
            <span class="codex-usage-hud-background-notification-count"
              data-field="backgroundUsageNotificationCount" hidden></span>
          </button>
        `;
      }

      function settingsButtonMarkup() {
        // The pricing-alert dot mirrors background pricing-sync differences. It
        // stays hidden until the settings domain reports unread changes.
        return `<button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙<span class="codex-usage-hud-settings-alert-dot" data-pricing-settings-dot="true" aria-hidden="true" hidden></span></button>`;
      }

      function panelMarkup(name, glyph, ariaLabel) {
        const glyphMarkup = glyph ? `<span class="codex-usage-hud-glyph">${glyph}</span>` : "";
        const panelSettingsMarkup = name === "top" ? settingsButtonMarkup() : "";
        const tokenBadgeMarkup = name === "request"
          ? (composerBadgeEnabled
            ? `<span class="codex-usage-hud-token-badge" data-composer-badge="idle"><span class="codex-usage-hud-token-badge-text" data-field="requestComposerTokens">TikToken:0 Ts</span></span>`
            : "")
          : "";
        const updateButtonMarkup = name === "top"
          ? `<button class="codex-usage-hud-update-button" data-action="update-action" title="" aria-label="" hidden>↓</button>`
          : "";
        const leftControlsMarkup = name === "top"
          ? `<div class="codex-usage-hud-left-controls">${updateButtonMarkup}</div>`
          : (name === "request"
            ? `<span class="codex-usage-hud-connection-dot" data-field="connectionDot" data-state="ok" title="CDP 连接正常" aria-label="CDP 连接正常" role="img"></span>`
            : "");
        const backgroundNotificationMarkup = name === "request"
          ? backgroundUsageNotificationMarkup()
          : "";
        return `
          <div class="codex-usage-hud-panel ${PANEL[name].className}" data-panel="${name}" data-expanded="false" role="status" aria-live="polite">
            ${resizeEdgesMarkup()}
            <div class="codex-usage-hud-collapsed" data-has-settings="${name === "top" ? "true" : "false"}" data-has-badge="${name === "request" && composerBadgeEnabled ? "true" : "false"}">
              ${leftControlsMarkup}
              <button class="codex-usage-hud-main" data-action="toggle" data-has-glyph="${glyph ? "true" : "false"}" aria-label="${ariaLabel}">
                ${glyphMarkup}
                ${name === "top" ? `<span class="codex-usage-hud-progress-strip-viewport"><span class="codex-usage-hud-progress-strip" data-field="topCollapsedProgress"></span></span>` : ""}
                <span class="codex-usage-hud-line" data-field="${name}Line"></span>
              </button>
              ${panelSettingsMarkup}
              ${tokenBadgeMarkup}
              ${backgroundNotificationMarkup}
            </div>
            ${name === "top" ? topExpandedMarkup() : requestExpandedMarkup()}
          </div>
        `;
      }

      function topExpandedMarkup() {
        return `
          <div class="codex-usage-hud-expanded-shell">
            <div class="codex-usage-hud-panel-header" data-action="toggle">
              <div class="codex-usage-hud-left-controls">
                <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
                <button class="codex-usage-hud-update-button" data-action="update-action" title="" aria-label="" hidden>↓</button>
              </div>
              <div class="codex-usage-hud-title" data-action="toggle" data-field="topTitle"></div>
              <div class="codex-usage-hud-session-meta" data-field="topSession"></div>
              <div class="codex-usage-hud-cache-pill" data-field="topCacheProgress"></div>
              ${settingsButtonMarkup()}
            </div>
            <div class="codex-usage-hud-top-body">
              <div class="codex-usage-hud-alert" data-field-panel="topWarnings" hidden>
                <span class="codex-usage-hud-alert-dot"></span>
                <span class="codex-usage-hud-alert-title">预警</span>
                <span class="codex-usage-hud-value warn" data-field="topWarnings"></span>
                <button class="codex-usage-hud-alert-close" data-action="dismiss-warnings-today" type="button" title="今天不再显示" aria-label="今天不再显示预警">×</button>
              </div>
              <div class="codex-usage-hud-top-grid">
                <div class="codex-usage-hud-top-column codex-usage-hud-top-column-left">
                  <section class="codex-usage-hud-top-card">
                    <div class="codex-usage-hud-card-head">
                      <div class="codex-usage-hud-card-title">本会话用量</div>
                      <div class="codex-usage-hud-card-actions">
                        <div class="codex-usage-hud-chip" data-field="topSessionRounds"></div>
                        <div class="codex-usage-hud-chip" data-field="topTaskOrdinalSession"></div>
                      </div>
                    </div>
                    <div class="codex-usage-hud-session-stats">
                      <div class="codex-usage-hud-session-stat">
                        <div class="codex-usage-hud-stat-value" data-field="topSessionCost"></div>
                        <div class="codex-usage-hud-stat-label">会话金额</div>
                      </div>
                      <div class="codex-usage-hud-session-stat">
                        <div class="codex-usage-hud-stat-value info" data-field="topSessionTokens"></div>
                        <div class="codex-usage-hud-stat-label">累计 tokens</div>
                      </div>
                    </div>
                    <div class="codex-usage-hud-session-insight">
                      <div class="codex-usage-hud-label">会话构成</div>
                      <div class="codex-usage-hud-value mono blue" data-field="topSessionMix"></div>
                      <div class="codex-usage-hud-value mono accent" data-field="topSessionAverage"></div>
                    </div>
                    <div class="codex-usage-hud-session-composition" data-field="topSessionComposition"></div>
                    <div class="codex-usage-hud-token-breakdown">
                      <div class="codex-usage-hud-token-chip"><span>输入</span><b data-field="topSessionInputTokens"></b></div>
                      <div class="codex-usage-hud-token-chip"><span>缓存</span><b data-field="topSessionCachedTokens"></b></div>
                      <div class="codex-usage-hud-token-chip"><span>输出</span><b data-field="topSessionOutputTokens"></b></div>
                      <div class="codex-usage-hud-token-chip"><span>推理</span><b data-field="topSessionReasoningTokens"></b></div>
                    </div>
                  </section>
                  <section class="codex-usage-hud-top-card">
                    <div class="codex-usage-hud-card-head">
                      <div class="codex-usage-hud-card-title">额度进度</div>
                    </div>
                    <div class="codex-usage-hud-budget-rails" data-field="topBudgetProgress"></div>
                  </section>
                  <section class="codex-usage-hud-top-card codex-usage-hud-heavy-rounds-card">
                    <div class="codex-usage-hud-card-head">
                      <div class="codex-usage-hud-card-title">高消耗轮次</div>
                      <div class="codex-usage-hud-chip" data-field="topHeavyRoundsSummary"></div>
                    </div>
                    <div class="codex-usage-hud-heavy-rounds" data-field="topHeavyRounds"></div>
                  </section>
                </div>
                <div class="codex-usage-hud-top-column codex-usage-hud-top-column-right">
                  <section class="codex-usage-hud-top-card codex-usage-hud-activity-card">
                    <div class="codex-usage-hud-card-head">
                      <div class="codex-usage-hud-card-title">当前活动</div>
                      <div class="codex-usage-hud-card-actions">
                        <div class="codex-usage-hud-activity-task-nav" data-field="topActivityTaskNav" hidden>
                          <button class="codex-usage-hud-activity-task-button" data-action="activity-task-prev" type="button" title="上一项需求" aria-label="上一项需求" disabled>
                            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m15 18-6-6 6-6"></path></svg>
                          </button>
                          <span class="codex-usage-hud-chip codex-usage-hud-activity-task-index" data-field="topActivityTaskOrdinal"></span>
                          <button class="codex-usage-hud-activity-task-button" data-action="activity-task-next" type="button" title="下一项需求" aria-label="下一项需求" disabled>
                            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 18 6-6-6-6"></path></svg>
                          </button>
                        </div>
                        <div class="codex-usage-hud-chip" data-field="topTaskOrdinalActivity"></div>
                        <div class="codex-usage-hud-chip" data-tone="warning" data-field="topActivityState"></div>
                      </div>
                    </div>
                    <div class="codex-usage-hud-activity-step">
                      <div class="codex-usage-hud-section-title" data-field="topCurrentTaskLabel">当前需求</div>
                      <div class="codex-usage-hud-value" data-field="topCurrentTask"></div>
                    </div>
                    <div class="codex-usage-hud-activity-main">
                      <div class="codex-usage-hud-section-title" data-field="topExecutingLabel">正在执行</div>
                      <div class="codex-usage-hud-value blue" data-field="topExecuting"></div>
                    </div>
                    <div class="codex-usage-hud-activity-metrics">
                      <div class="codex-usage-hud-activity-metric">
                        <div class="codex-usage-hud-section-title" data-field="topActivityElapsedLabel">已运行</div>
                        <div class="codex-usage-hud-value mono" data-field="topActivityElapsed"></div>
                      </div>
                      <div class="codex-usage-hud-activity-metric">
                        <div class="codex-usage-hud-section-title" data-field="topActivityGapLabel">当前等待</div>
                        <div class="codex-usage-hud-value mono" data-field="topActivityGap"></div>
                      </div>
                      <div class="codex-usage-hud-activity-metric">
                        <div class="codex-usage-hud-section-title" data-field="topActivityLastLabel">需求轮次</div>
                        <div class="codex-usage-hud-value mono" data-field="topActivityLast"></div>
                      </div>
                    </div>
                    <div class="codex-usage-hud-activity-trail">
                      <div class="codex-usage-hud-activity-trail-head">
                        <div class="codex-usage-hud-section-title">活动轨迹</div>
                        <div class="codex-usage-hud-card-actions">
                          <div class="codex-usage-hud-chip codex-usage-hud-copy-chip" data-field="topSlow"></div>
                          <div class="codex-usage-hud-chip codex-usage-hud-copy-chip" data-field="topGap"></div>
                        </div>
                      </div>
                      <div class="codex-usage-hud-activity-timeline" data-field="topActivityTrail"></div>
                      <button class="codex-usage-hud-activity-load-more" data-field="topActivityLoadMore" data-action="activity-load-more" data-page-size="12" type="button">查看更多</button>
                    </div>
                  </section>
                </div>
              </div>
            </div>
            ${topExpandedResizeMarkup()}
          </div>
        `;
      }

      function requestExpandedMarkup() {
        return `
          <div class="codex-usage-hud-expanded-shell">
            <div class="codex-usage-hud-request-subhead"><span>轮次流水</span><span>最新在上</span></div>
            <div class="codex-usage-hud-request-list" data-field="requestRows"></div>
            <div class="codex-usage-hud-active-session-candidates" data-field="activeSessionCandidates" hidden></div>
            <div class="codex-usage-hud-panel-header" data-action="toggle">
              <div class="codex-usage-hud-left-controls">
                <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
                <span class="codex-usage-hud-connection-dot" data-field="connectionDot" data-state="ok" title="CDP 连接正常" aria-label="CDP 连接正常" role="img"></span>
              </div>
              <div class="codex-usage-hud-title codex-usage-hud-line" data-action="toggle" data-field="requestLineExpanded"></div>
              ${backgroundUsageNotificationMarkup()}
            </div>
            ${requestExpandedResizeMarkup()}
          </div>
        `;
      }
"""

__all__ = ["TEXT"]
