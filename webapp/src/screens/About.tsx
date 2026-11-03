// «О центре»: текст о центре, площадки с картой и дорогой, связь с менеджером.

import type { ReactNode } from "react";

import { api } from "../api";
import { Icon } from "../icons";
import { openUrl } from "../telegram";
import { Button, ErrorState, Loading, ManagerButton, Rich, useLoad } from "../ui";
import { Brand } from "./Schedule";

export function AboutScreen(): ReactNode {
  const about = useLoad(api.about);
  return (
    <div className="page">
      <Brand />
      {about.loading && !about.data ? (
        <Loading rows={2} />
      ) : about.error || !about.data ? (
        <ErrorState error={about.error!} onRetry={about.reload} />
      ) : (
        <>
          <div className="card card-body">
            <Rich html={about.data.html} />
          </div>
          {about.data.venues.length > 0 && (
            <>
              <div className="section-title">Где проходят занятия</div>
              <div className="stack">
                {about.data.venues.map((venue) => (
                  <div className="card card-body" key={venue.id}>
                    <div className="row" style={{ alignItems: "flex-start" }}>
                      <span className="inline-icon">
                        <Icon.pin />
                      </span>
                      <div className="grow">
                        <div style={{ fontWeight: 600 }}>{venue.name}</div>
                        <div className="muted">{venue.address}</div>
                        {venue.directions_html && <Rich html={venue.directions_html} className="small" />}
                      </div>
                    </div>
                    {venue.map_url && (
                      <div style={{ marginTop: 10 }}>
                        <Button size="small" variant="secondary" onClick={() => openUrl(venue.map_url!)}>
                          Открыть на карте
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="stack" style={{ marginTop: 16 }}>
            <ManagerButton url={about.data.manager_url} label="Связаться с менеджером" />
          </div>
        </>
      )}
    </div>
  );
}
