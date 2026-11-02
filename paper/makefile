file=main
out=Muthuphei_Mukhunyeledzi_2596286

all:
	pdflatex -jobname=$(out) $(file).tex
	bibtex $(out)
	pdflatex -jobname=$(out) $(file).tex
	pdflatex -jobname=$(out) $(file).tex

clean:
	rm -f *.aux *.bbl *.blg *.log *.out
