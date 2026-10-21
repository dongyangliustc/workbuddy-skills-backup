#Convert geometry (final, input orientation) in all Gaussian .out files in current folder to .gjf file by Multiwfn
#!/bin/bash
icc=0
nfile=`ls *.out|wc -l`
for inf in *.fchk
do
((icc++))
echo Converting ${inf} to ${inf//fchk/inp} ... \($icc of $nfile\)
/home/feng2011group/lidongyang2023/software/Multiwfn_3.8_dev_bin_Linux_noGUI/Multiwfn ${inf} << EOF > /dev/null
oi
${inf//fchk/inp}
-100
/home/feng2011group/lidongyang2023/software/Multiwfn_3.8_dev_bin_Linux_noGUI/template.inp
q
EOF
done
