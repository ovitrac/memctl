# Conception de la Sécurité

## Authentification

Le système utilise des jetons JWT pour l'authentification sans état.
Chaque requête est vérifiée par le middleware d'authentification avant
d'accéder aux ressources protégées.

## Contrôle d'Accès

Le modèle RBAC (contrôle d'accès basé sur les rôles) définit trois niveaux :
- Administrateur : accès complet au système et gestion des utilisateurs
- Éditeur : lecture et écriture sur les ressources du projet
- Lecteur : consultation seule, aucune modification possible

## Chiffrement des Données

Les données sensibles sont chiffrées avec AES-256 au repos.
Les clés de chiffrement sont stockées dans un coffre-fort sécurisé
(HashiCorp Vault) avec rotation automatique tous les 90 jours.

## Traçabilité

Tous les événements d'authentification sont enregistrés :
- Horodatage précis et adresse IP source
- Résultat de la tentative (succès ou échec)
- Géolocalisation pour la détection d'anomalies

## Conformité Réglementaire

Le système respecte les exigences du RGPD :
- Droit à l'effacement des données personnelles
- Portabilité des données au format standard
- Registre des traitements maintenu à jour
- Délégué à la protection des données désigné
