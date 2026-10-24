# Backups Pterodactyl → Nextcloud

Crée des backups de tes serveurs Pterodactyl (Minecraft ou autre), les envoie sur Nextcloud
et ne garde que les N derniers par serveur.

Structure sur Nextcloud :

```
Backups/
├── Survie/
│   ├── Survie_2026-10-22_04-00-00.tar.gz
│   ├── Survie_2026-10-23_04-00-00.tar.gz
│   └── Survie_2026-10-24_04-00-00.tar.gz
└── Creatif/
    └── Creatif_2026-10-24_04-00-00.tar.gz
```

## Prérequis

1. **Pterodactyl** : crée une clé API *Client* (Compte → API Credentials).
   Chaque serveur doit avoir une limite de backups ≥ 1 (Admin → Serveur → Build Configuration → Backup Limit).
   Pour sauvegarder *tous* les serveurs du panel, utilise une clé de compte admin avec `PTERO_LIST_TYPE=admin-all`.
2. **Nextcloud** : crée un mot de passe d'application (Paramètres → Sécurité → Appareils et sessions).
3. Le conteneur doit pouvoir joindre le panel **et** l'URL publique de chaque node Wings (le téléchargement se fait directement depuis Wings).

## Installation

```bash
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f
```

## Test manuel

```bash
# Sauvegarde immédiate de tous les serveurs
docker compose run --rm ptero-nc-backup python -m app.main --once

# Un seul serveur (nom ou identifiant)
docker compose run --rm ptero-nc-backup python -m app.main --once --server Survie
```

## Notes

- Les backups créés sur le panel sont préfixés `nc-auto-` ; ils sont supprimés après envoi (sauf `KEEP_PANEL_BACKUP=true`)
  afin de ne pas occuper le quota de backups de chaque serveur. Seuls ces backups sont touchés, jamais les tiens.
- La rotation n'a lieu qu'après un envoi vérifié (taille comparée) : un échec ne supprime jamais d'anciens backups.
- Les fichiers de plus de `NC_CHUNK_THRESHOLD_MB` sont envoyés par morceaux (contourne les limites nginx/PHP/Cloudflare).
- L'espace disque temporaire nécessaire = taille du plus gros backup (un seul serveur à la fois).

## Arrêt automatique des serveurs vides

Activé avec `AUTOSTOP_ENABLED=true`. Toutes les `AUTOSTOP_CHECK_MINUTES` minutes, chaque serveur **en marche** est pingé
(Server List Ping Minecraft) pour connaître le nombre de joueurs :

- **0 joueur** et pas de décompte en cours : un décompte de `AUTOSTOP_EMPTY_HOURS` heures démarre.
- **Au moins 1 joueur** : le décompte est remis à zéro.
- **Décompte écoulé** et serveur toujours vide : arrêt **propre** (signal `stop`).

Le début du décompte est enregistré dans `/data/autostop_state.json` (volume Docker), il survit donc à un redémarrage du conteneur.

Sécurités : aucun arrêt si le ping échoue (serveur non-Minecraft, port injoignable, `enable-status=false`),
aucun arrêt pendant une sauvegarde, jamais de `kill`. Les serveurs arrêtés ne sont pas redémarrés automatiquement.

Test sans risque : `AUTOSTOP_DRY_RUN=true`, puis
`docker compose run --rm ptero-nc-backup python -m app.main --autostop`
