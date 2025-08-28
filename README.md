# Moyennisator 3000

Un analyseur de notes Pronote sécurisé avec interface web moderne pour analyser les performances académiques françaises et calculer les statistiques du Brevet.

## 🎯 Aperçu

Moyennisator 3000 est une application web Flask élégante qui se connecte à votre compte Pronote pour analyser automatiquement vos évaluations et calculer vos moyennes par matière ainsi que vos statistiques du Brevet. L'application offre une interface utilisateur intuitive et responsive avec des graphiques visuels attrayants.

## ✨ Fonctionnalités

### 🔐 Sécurité et Authentification
- **Gestion sécurisée des identifiants** : Utilisation de variables d'environnement ou saisie sécurisée
- **Connexion directe à Pronote** : Intégration avec l'API officielle Pronote via pronotepy
- **Protection CSRF** : Sécurisation des formulaires web
- **Aucun stockage des mots de passe** : Les identifiants ne sont jamais sauvegardés

### 📊 Analyse Académique Complète
- **Évaluations individuelles** : Affichage détaillé de chaque évaluation avec notes et coefficients
- **Moyennes par matière** : Calcul automatique des moyennes pondérées par coefficient
- **Statistiques du Brevet** : Calcul complet des points du socle commun (sur 400)
- **Niveau de performance** : Évaluation automatique du niveau (Très Bien, Bien, Assez Bien, etc.)
- **Système de notation française** : Conversion des grades V+, V, J, R en points

### 🎨 Interface Web Moderne
- **Design responsive** : Compatible mobile, tablette et ordinateur
- **Interface Bootstrap 5** : Design moderne et professionnel
- **Thème dégradé** : Couleurs attrayantes avec effets visuels
- **Cartes interactives** : Affichage organisé des données avec animations
- **Badges colorés** : Visualisation intuitive des notes par couleur
- **Barres de progression** : Représentation visuelle des moyennes

### 📈 Données et Statistiques
- **Analyse en temps réel** : Connexion directe aux données Pronote actuelles
- **Calculs automatiques** : Moyennes pondérées et statistiques Brevet
- **Export JSON** : API REST pour récupération des données
- **Logging complet** : Suivi détaillé des opérations et erreurs

## 🚀 Installation et Utilisation

### Prérequis
```bash
Python 3.11+
pip (gestionnaire de paquets Python)
```

### Installation des dépendances
```bash
pip install pronotepy python-dotenv flask flask-wtf
```

### Démarrage de l'application web
```bash
python app.py
```

L'application sera accessible sur `http://localhost:5000`

### Utilisation en ligne de commande (optionnel)
```bash
python pronote_analyzer.py
```

## 🏫 Configuration

### URL Pronote
L'application est préconfigurée pour se connecter à :
```
https://4170004n.index-education.net/pronote/eleve.html
```

### Variables d'environnement (optionnel)
Créez un fichier `.env` pour éviter de saisir vos identifiants à chaque connexion :
```env
PRONOTE_URL=https://4170004n.index-education.net/pronote/eleve.html
PRONOTE_USERNAME=votre_nom_utilisateur
PRONOTE_PASSWORD=votre_mot_de_passe
```

## 🎓 Système de Notation

### Conversion des grades en points
- **V+** : 50 points (Excellent)
- **V** : 40 points (Très bien)  
- **J** : 25 points (Satisfaisant)
- **R** : 10 points (Insuffisant)

### Calcul du Brevet
- **Moyenne points** : Moyenne pondérée sur 50 points
- **Moyenne sur 20** : Conversion en notation traditionnelle
- **Socle sur 400** : Score total pour le Brevet des Collèges

### Niveaux de performance
- **350+ points** : Excellent (Mention Très Bien possible)
- **280-349 points** : Bien (Mention Bien possible)
- **240-279 points** : Satisfaisant (Mention Assez Bien possible)
- **200-239 points** : Niveau de réussite
- **< 200 points** : En dessous du niveau de réussite

## 🛠️ Architecture Technique

### Backend
- **Framework** : Flask (Python web framework)
- **API Pronote** : pronotepy (bibliothèque officielle)
- **Formulaires** : Flask-WTF avec validation
- **Logging** : Système de logs complet
- **Sécurité** : Gestion sécurisée des sessions

### Frontend
- **Framework CSS** : Bootstrap 5.1.3
- **Icônes** : Font Awesome 6.0
- **JavaScript** : Vanilla JS pour interactions
- **Design** : Responsive et moderne
- **Animations** : Transitions CSS fluides

### Base de données
- **Stockage** : En mémoire (session temporaire)
- **Aucune persistance** : Les données ne sont pas sauvegardées pour la sécurité

## 📱 Fonctionnalités de l'Interface

### Page de connexion
- Formulaire sécurisé avec validation
- Messages d'erreur contextuels
- Indicateur de chargement
- Design responsive

### Page de résultats
- Vue d'ensemble des statistiques
- Graphiques de performance
- Tableau détaillé des évaluations
- Navigation intuitive
- Bouton retour vers connexion

## 🔍 API REST

### Endpoint de données
```
GET /api/data
```

Retourne les données au format JSON :
```json
{
  "evaluations": [...],
  "subject_averages": {...},
  "brevet_stats": {...},
  "total_evaluations": 81
}
```

## 📝 Logs et Debugging

### Fichiers de logs
- `pronote_web.log` : Logs de l'application web
- `pronote_analyzer.log` : Logs de l'analyseur en ligne de commande

### Informations loggées
- Tentatives de connexion
- Récupération des évaluations
- Erreurs de validation
- Calculs de moyennes

## 🚨 Sécurité

### Bonnes pratiques implémentées
- Pas de stockage des mots de passe
- Validation des formulaires
- Gestion sécurisée des sessions
- Logs d'audit complets
- Variables d'environnement pour les secrets

## 🐛 Dépannage

### Problèmes courants

**Erreur de connexion Pronote**
- Vérifiez vos identifiants
- Assurez-vous que Pronote est accessible
- Vérifiez l'URL de votre établissement

**Pas d'évaluations trouvées**
- Vérifiez que des évaluations sont disponibles sur Pronote
- Essayez de vous reconnecter

**Erreur de démarrage de l'application**
- Vérifiez que toutes les dépendances sont installées
- Assurez-vous que le port 5000 est libre

## 🤝 Contribution

Ce projet utilise :
- Python 3.11+
- Flask pour le backend
- Bootstrap 5 pour l'interface
- pronotepy pour l'intégration Pronote

## 📄 Licence

Application développée pour l'analyse académique personnelle. Utilisation des données Pronote conforme aux conditions d'utilisation de la plateforme.

---

**Moyennisator 3000** - L'analyseur de notes Pronote nouvelle génération 🎓
