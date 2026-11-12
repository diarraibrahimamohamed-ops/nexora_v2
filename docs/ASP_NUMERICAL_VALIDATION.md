# Garde-fous numériques de l'agrégation ASP

ASP agrège des scores empiriques issus de Vina. Sa température est un
paramètre d'échelle de pondération, pas une température thermodynamique qui
transformerait les scores Vina en énergie libre expérimentale.

Les entrées sont désormais contrôlées avant le calcul :
- température convertible en nombre, finie et strictement positive;
- score Vina convertible en nombre et fini;
- seuil RMSD convertible en nombre, fini et strictement positif;
- poses possédant au moins une coordonnée d'atome lourd finie.

Les valeurs `NaN`, `+/-Infinity`, zéro, les valeurs négatives et les textes non
numériques sont rejetés avec une erreur explicite. La même validation de
température est utilisée par les fonctions de scoring et par l'agrégation
globale avant le clustering, y compris pour une entrée contenant une seule pose.

Cette protection garantit la stabilité des entrées numériques; elle ne
constitue pas une validation physique de la méthode de pondération de
Boltzmann. Le résultat ASP reste un score agrégé post-Vina, et non une ΔG
expérimentale.
