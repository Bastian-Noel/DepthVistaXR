# DepthVista XR

[English documentation](../README.md)

DepthVista XR capture un écran ou une fenêtre Windows, estime sa profondeur,
génère une image stéréoscopique en temps réel et l’affiche dans un casque à
travers OpenXR.

Cette version est une adaptation de **nunif/IW3** avec une interface Dear
PyGui, une sortie OpenXR directe, un écran virtuel plat ou incurvé et des
contrôles pour manettes VR.

## Configuration requise

- Windows 10 ou Windows 11 64 bits.
- Casque compatible OpenXR.
- Runtime OpenXR PC installé et actif.
- Pour Meta Quest : application Meta Quest Link installée et casque connecté
  par câble Link ou Air Link.
- Carte NVIDIA compatible CUDA recommandée.
- Pilotes graphiques récents.
- Environ 9 Go d’espace disque pour le paquet actuel et les modèles présents.

Les paquets distribués sont portables et contiennent Python dans `runtime`.
Un clone du dépôt GitHub exclut volontairement le runtime et les poids des
modèles.

## Installation rapide

### Paquet distribué

1. Extraire ou copier le dossier complet dans un emplacement accessible, par
   exemple `C:\DepthVista-XR`.
2. Ne pas déplacer séparément `DepthVista-XR.bat` : les dossiers `app`,
   `runtime` et `scripts` doivent rester à côté de lui.
3. Installer le logiciel PC du casque et activer son runtime OpenXR.
4. Connecter et démarrer le casque en mode PC VR.
5. Double-cliquer sur `DepthVista-XR.bat`.

Éviter `Program Files` si Windows bloque l’écriture de la configuration ou des
modèles. Un dossier utilisateur comme `C:\Users\<nom>\Applications\DepthVista-XR`
convient mieux.

### Clone GitHub

1. Cloner ou télécharger le dépôt.
2. Exécuter `install.bat` une fois pour télécharger Python 3.12 portable et
   installer les dépendances de `requirements.txt`.
3. Exécuter `DepthVista-XR.bat`.
4. Télécharger ensuite les modèles optionnels nécessaires.

Le dossier `runtime`, les modèles téléchargés et la configuration locale sont
exclus de Git.

## Mise en place avec un Meta Quest

1. Installer et lancer Meta Quest Link sur le PC.
2. Connecter le Quest par câble USB Link ou activer Air Link.
3. Dans les paramètres de Meta Quest Link, définir Meta Quest Link comme
   runtime OpenXR actif si nécessaire.
4. Entrer dans l’environnement Quest Link depuis le casque.
5. Lancer `DepthVista-XR.bat` sur le PC.
6. Sélectionner la source et cliquer sur **Démarrer OpenXR**.

Si l’application affiche qu’aucun runtime OpenXR n’est disponible, vérifier
que le logiciel du casque est lancé et que son runtime est bien actif avant de
redémarrer DepthVista XR.

## Première utilisation

Dans l’onglet **Général** :

1. Choisir un profil.
2. Choisir **Écran complet** ou **Fenêtre**.
3. Sélectionner l’écran ou la fenêtre dans la liste.
4. Contrôler la source avec l’aperçu.
5. Choisir la résolution générée, généralement `1080`.
6. Conserver la méthode de capture sélectionnée automatiquement.
7. Régler la force 3D avec la valeur personnalisée ou les préréglages
   `1`, `1.5` et `2`.
8. Ouvrir l’onglet **OpenXR** pour régler l’écran virtuel.
9. Cliquer sur **Démarrer OpenXR**.

Pendant une session, l’interface normale est remplacée par une interface
réduite contenant uniquement les paramètres modifiables en temps réel et le
bouton **ARRÊTER OPENXR**.

## Langues de l’interface

Le sélecteur **Langue** est affiché en haut de l’application. L’anglais est la
langue par défaut. Le français, l’espagnol et le chinois simplifié sont
également disponibles. Le choix est enregistré dans
`app\tmp\depthvista-xr.json`.

## Profils

| Profil | Modèle | Méthode 3D | Profondeur | FPS cible | Utilisation |
|---|---|---|---:|---:|---|
| Fluide | Distill Any Depth Small | `mlbw_l2s` | 392 | 60 | GPU plus limité, priorité à la fluidité |
| Équilibré | Video Depth Anything Stream Small | `row_flow_v3` | 512 | 60 | Réglage général recommandé |
| Meilleure qualité | Video Depth Anything Stream Small | `row_flow_v3_sym` | 720 | 30 | Image plus précise, charge GPU supérieure |

La **résolution générée** (`720`, `900` ou `1080`) correspond à la hauteur de
l’image stéréoscopique envoyée à OpenXR. La **résolution profondeur** correspond
à la résolution utilisée par le modèle d’estimation de profondeur. Ce sont
deux réglages différents.

## Méthodes de capture

| Méthode | Fonctionnement | Avantage | Limite |
|---|---|---|---|
| `wc_cuda` | Windows Capture vers CUDA | Plus rapide sur NVIDIA, copie CPU réduite | Requiert CUDA et `wc_cuda` |
| `wc_mp` | Windows Capture dans un processus séparé | Bonne fluidité et bonne isolation | Copie supplémentaire par rapport à CUDA |
| `mss` | Capture multiplateforme par mémoire écran | Compatible et stable | Généralement moins rapide |
| `pil` | Capture avec Pillow/ImageGrab | Méthode de secours simple | La plus lente |

DepthVista XR choisit par défaut `wc_cuda` lorsqu’il est disponible, sinon
`wc_mp`, puis `mss`.

## Réglages OpenXR

- **Projection** : écran plat ou incurvé.
- **Distance** : distance virtuelle entre l’utilisateur et l’écran.
- **Largeur écran** : taille horizontale de l’écran en mètres.
- **Courbure** : angle de courbure de l’écran.
- **Afficher H/G FPS** : affiche les fréquences casque, génération et capture.
- **Force de la 3D** : augmente ou réduit la séparation stéréoscopique.
- **Convergence** : déplace le plan de profondeur perçu.

La source, le modèle, la méthode 3D et la résolution générée doivent être
choisis avant le démarrage. La distance, la taille, la courbure, la force 3D
et les contrôles peuvent être ajustés pendant la session.

## Contrôles des manettes

### Mode Bureau

- Pointeur visible à l’écran.
- Gâchette haute : clic gauche et glisser-déposer.
- Grip sans mouvement : clic droit si l’option est activée.
- `A/X` : lecture ou pause.
- `B/Y` : arrêter la session.
- Joystick gauche/droite : flèches vidéo gauche/droite.
- Joystick haut/bas : zoom avant/arrière.
- Grip + joystick gauche/droite : modifier la courbure.
- Grip + joystick haut/bas : agrandir ou réduire l’écran.
- Clic sur le joystick : recentrer l’écran.

### Mode Cinéma

- Aucun pointeur souris.
- Gâchette haute : lecture ou pause.
- Les autres raccourcis de navigation, taille et recentrage restent
  disponibles.

## Modèles

Tous les modèles sont optionnels et conservent leur propre licence. Une
installation peut déjà contenir des modèles téléchargés dans :

`app\iw3\pretrained_models`

Pour télécharger ou réparer les modèles pris en charge :

```bat
cd /d C:\DepthVista-XR\app
..\runtime\python\python.exe -X utf8 -m iw3.download_models
```

Adapter `C:\DepthVista-XR` au dossier réel. Le téléchargement peut être
volumineux. Certains modèles sont réservés à un usage non commercial ;
consulter `LICENSE` avant redistribution ou utilisation commerciale.

## Contenu du projet

```text
DepthVista-XR.bat       Lanceur principal
app/
  iw3/                  IW3 modifié pour DepthVista XR
  nunif/                Moteur commun nunif requis par IW3
  tmp/                  Configuration locale de l’application
runtime/
  python/               Python portable et dépendances
scripts/
  setup-env.bat         Prépare l’environnement portable
  launch-debug.bat      Lance avec une console de diagnostic
licenses/               Licences tierces conservées
LICENSE                 Portée des licences du projet et des modèles
requirements.txt        Dépendances globales Windows/CUDA
```

`app\iw3` contient les modifications spécifiques à DepthVista XR.
`app\nunif` reste le socle technique nécessaire au chargement des modèles,
aux traitements PyTorch et aux utilitaires communs.

## Reconstruction du runtime

Cette section sert aux développeurs ou à la réparation d’un paquet incomplet.
Pour une utilisation normale, conserver le dossier `runtime` fourni.

1. Installer ou placer Python 3.12 64 bits dans `runtime\python`.
2. Ouvrir PowerShell à la racine du projet.
3. Installer les dépendances :

```powershell
$python = ".\runtime\python\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r ".\requirements.txt"
```

Les versions actuellement utilisées pour la sortie OpenXR sont notamment
`pyopenxr 1.1.5301`, `glfw 2.10.0`, `PyOpenGL 3.1.10`,
`Dear PyGui 2.3.1` et `PyTorch 2.7.1 + CUDA 12.8`.

Le manifeste global cible actuellement Windows et CUDA 12.8. Pour une autre
carte ou une autre version CUDA, il faut adapter `requirements.txt`
et vérifier les dépendances de capture GPU.

## Diagnostic

Si le lanceur principal se ferme sans message :

```bat
scripts\launch-debug.bat
```

Contrôles utiles :

```bat
runtime\python\python.exe -X utf8 -c "import iw3.desktop.gui_dpg; print('Import OK')"
runtime\python\python.exe -X utf8 -c "from iw3.desktop.openxr_output import detect_openxr_runtime; print(detect_openxr_runtime())"
```

### Écran noir ou aucune image dans le casque

- Vérifier que le casque est déjà connecté en PC VR.
- Vérifier le runtime OpenXR actif.
- Tester une application OpenXR connue.
- Essayer une source **Écran complet** plutôt qu’une fenêtre.
- Essayer `wc_mp`, puis `mss`.
- Fermer les overlays ou logiciels de capture concurrents.

### Artefacts noirs pendant les mouvements

- Tester la résolution générée `720`, puis `900`.
- Utiliser le profil **Fluide** ou **Équilibré**.
- Réduire la résolution profondeur.
- Tester `wc_cuda` puis `wc_mp`.
- Mettre à jour le pilote graphique et le logiciel du casque.
- Vérifier que le GPU n’est pas limité par sa température ou sa mémoire.

### Performances insuffisantes

- Utiliser le profil **Fluide**.
- Réduire la résolution générée.
- Fermer les applications GPU lourdes.
- Préférer `wc_cuda` sur une carte NVIDIA compatible.
- Désactiver les overlays inutiles.

## Vidéos protégées et DRM

DepthVista XR effectue une capture normale de l’écran ou d’une fenêtre. Il ne
contourne pas les protections DRM. Netflix, Prime Video et d’autres services
peuvent volontairement produire une zone noire dans les logiciels de capture.
Le résultat dépend du navigateur, de l’accélération matérielle, du service et
des règles DRM appliquées au contenu.

Désactiver l’accélération matérielle peut modifier le comportement de certains
navigateurs, mais ce fonctionnement n’est pas garanti et ne doit pas être
utilisé pour contourner une protection technique. Utiliser les applications,
fonctions hors ligne et modes de lecture autorisés par le fournisseur.

## Configuration et réinitialisation

La configuration principale est enregistrée dans :

`app\tmp\depthvista-xr.json`

Pour réinitialiser uniquement les préférences DepthVista XR :

1. Fermer l’application.
2. Renommer ou supprimer `app\tmp\depthvista-xr.json`.
3. Relancer `DepthVista-XR.bat`.

Ne pas supprimer `app\iw3\pretrained_models` sauf si les modèles doivent être
retéléchargés.

## Licences

Consulter `LICENSE` et le dossier `licenses`.
Les composants nunif, IW3, XRPlay adaptés et les modèles peuvent utiliser des
licences différentes.
